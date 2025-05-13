import os
import uuid
from flask import Flask, render_template, url_for, request, redirect, flash
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from bson.objectid import ObjectId # For deleting by _id
from datetime import datetime

app = Flask(__name__)

# Configuration
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_key_for_dev') # For flash messages
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'anpr_db'
DETECTIONS_COLLECTION_NAME = 'detections'
VIDEO_JOBS_COLLECTION_NAME = 'video_jobs'
STREAM_CONFIGS_COLLECTION_NAME = 'stream_configs' # New collection for persistent streams

# Path for web-portal to save uploads. This is on the anpr_images volume.
# anpr_images is mounted at /app/static/detections in web-portal
# and at /app/detections in anpr-service.
UPLOAD_FOLDER_REL = 'detections/uploads' # Relative to static folder for web-portal
app.config['UPLOAD_FOLDER'] = os.path.join(app.static_folder, 'uploads') # /app/static/uploads - wait, this is wrong.
# UPLOAD_FOLDER should be /app/static/detections/uploads for web-portal to write into the volume.
# Let's define it more directly based on the volume mount.
# The volume anpr_images is mounted at /app/static/detections.
# So, files saved by web-portal to /app/static/detections/uploads/file.mp4
# will appear in anpr-service at /app/detections/uploads/file.mp4.
UPLOAD_SUBDIR = "uploads" # Subdirectory within the shared volume anpr_images
# Path for web-portal to save: /app/static/detections/uploads
PATH_FOR_WEBPORTAL_SAVE = os.path.join("/app/static/detections", UPLOAD_SUBDIR)
# Path for anpr-service to read: /app/detections/uploads (this is what goes into DB)
PATH_FOR_ANPR_SERVICE_READ_PREFIX = os.path.join("/app/detections", UPLOAD_SUBDIR)


ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}

client = None
db = None
detections_collection = None
video_jobs_collection = None
stream_configs_collection = None # For persistent stream configs

try:
    client = MongoClient(MONGO_URI)
    # The ismaster command is cheap and does not require auth.
    client.admin.command('ismaster')
    db = client[DB_NAME]
    detections_collection = db[DETECTIONS_COLLECTION_NAME]
    video_jobs_collection = db[VIDEO_JOBS_COLLECTION_NAME]
    stream_configs_collection = db[STREAM_CONFIGS_COLLECTION_NAME] # Initialize new collection
    app.logger.info("Successfully connected to MongoDB.")

    # Create upload directory if it doesn't exist
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try:
            os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
            app.logger.info(f"Created upload directory: {PATH_FOR_WEBPORTAL_SAVE}")
        except OSError as e:
            app.logger.error(f"Could not create upload directory {PATH_FOR_WEBPORTAL_SAVE}: {e}")
            # This could be a problem for uploads.

except ConnectionFailure:
    app.logger.error("MongoDB connection failed.")
except Exception as e:
    app.logger.error(f"An error occurred during MongoDB initialization: {e}")


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    detections = []
    stream_configs = []
    error_message = None

    if detections_collection is not None:
        try:
            # Fetch all detections, sort by timestamp descending
            detections_cursor = detections_collection.find().sort("timestamp", -1)
            for detection in detections_cursor:
                # Ensure image_path is correctly formatted for url_for
                # PDR image_path: "/detections/plate_..."
                # Static mount: anpr_images:/app/static/detections
                # So, if image_path is "/detections/img.jpg", it's at /app/static/detections/img.jpg
                # url_for('static', filename='detections/img.jpg') should work.
                # We need to strip the leading '/' if image_path is absolute like "/detections/..."
                if 'image_path' in detection and detection['image_path']:
                    if detection['image_path'].startswith('/'):
                        detection['image_url'] = url_for('static', filename=detection['image_path'][1:])
                    else:
                        detection['image_url'] = url_for('static', filename=detection['image_path'])
                else:
                    detection['image_url'] = None # Or a placeholder image
                detections.append(detection)
        except Exception as e:
            app.logger.error(f"Error fetching detections from MongoDB: {e}")
            error_message = "Error fetching data from database."
            detections = [] # Clear detections if error
    else:
        error_message = "Database connection not available for detections."
        app.logger.warning("Attempted to fetch detections, but DB connection is not available.")

    if stream_configs_collection is not None:
        try:
            stream_configs = list(stream_configs_collection.find().sort("name", 1))
        except Exception as e:
            app.logger.error(f"Error fetching stream configs: {e}")
            if not error_message: # Don't overwrite existing error
                 error_message = "Error fetching stream configurations."
            else:
                error_message += " Also, error fetching stream configurations."
    elif not error_message: # Only set this if no other error occurred
        error_message = "Database connection not available for stream configurations."
        app.logger.warning("Attempted to fetch stream configs, but DB connection is not available.")
    else: # Append to existing error
        error_message += " Also, database connection not available for stream configurations."


    return render_template('index.html', detections=detections, stream_configs=stream_configs, error_message=error_message)


@app.route('/upload', methods=['POST'])
def upload_video():
    if video_jobs_collection is None:
        flash('Database service not available for video uploads.', 'error')
        return redirect(url_for('index'))

    if 'video_file' not in request.files:
        flash('No video file part in the request.', 'error')
        return redirect(url_for('index'))
    
    file = request.files['video_file']
    if file.filename == '':
        flash('No video selected for uploading.', 'error')
        return redirect(url_for('index'))

    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        # Create a unique filename to avoid collisions and for security
        unique_suffix = uuid.uuid4().hex[:8]
        filename = f"{unique_suffix}_{original_filename}"
        
        try:
            # Ensure upload directory exists (it should have been created at startup)
            if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
                 os.makedirs(PATH_FOR_WEBPORTAL_SAVE) # Try to create again just in case

            save_path = os.path.join(PATH_FOR_WEBPORTAL_SAVE, filename)
            file.save(save_path)
            app.logger.info(f"Video saved to {save_path}")

            # Path for anpr-service to access the file via the shared volume
            anpr_service_video_path = os.path.join(PATH_FOR_ANPR_SERVICE_READ_PREFIX, filename)

            job_id = uuid.uuid4().hex
            job_record = {
                "job_id": job_id,
                "video_path": anpr_service_video_path, # Path for anpr-service
                "original_filename": original_filename,
                "status": "pending",
                "uploaded_at": datetime.utcnow(),
                "processed_at": None,
                "anpr_service_messages": []
            }
            video_jobs_collection.insert_one(job_record)
            flash(f"Video '{original_filename}' uploaded successfully and queued for processing (Job ID: {job_id}).", 'success')
            app.logger.info(f"Video job {job_id} for {anpr_service_video_path} added to MongoDB.")

        except FileNotFoundError: # If os.makedirs failed and PATH_FOR_WEBPORTAL_SAVE doesn't exist
            app.logger.error(f"Upload directory {PATH_FOR_WEBPORTAL_SAVE} does not exist. Cannot save file.")
            flash(f"Error: Upload directory not found on server.", 'error')
        except OperationFailure as e:
            app.logger.error(f"MongoDB error when adding video job: {e}")
            flash('Error queuing video for processing (database error).', 'error')
        except Exception as e:
            app.logger.error(f"Error uploading video: {e}", exc_info=True)
            flash(f"An unexpected error occurred: {e}", 'error')
            # Consider deleting the saved file if DB entry failed
            if 'save_path' in locals() and os.path.exists(save_path):
                try:
                    os.remove(save_path)
                    app.logger.info(f"Cleaned up partially uploaded file: {save_path}")
                except OSError as rm_e:
                    app.logger.error(f"Error cleaning up file {save_path}: {rm_e}")
        
        return redirect(url_for('index'))
    else:
        flash('Invalid file type. Allowed types are: ' + ", ".join(ALLOWED_EXTENSIONS), 'error')
        return redirect(url_for('index'))


@app.route('/add_stream', methods=['POST'])
def add_stream():
    if stream_configs_collection is None:
        flash('Database service not available for managing streams.', 'error')
        return redirect(url_for('index'))

    stream_name = request.form.get('stream_name')
    stream_url = request.form.get('stream_url')

    if not stream_name or not stream_url:
        flash('Stream name and URL are required.', 'error')
        return redirect(url_for('index'))

    try:
        # Check if stream name already exists to avoid duplicates, or allow them
        existing_stream = stream_configs_collection.find_one({"name": stream_name})
        if existing_stream:
            flash(f"A stream with the name '{stream_name}' already exists.", 'error')
            return redirect(url_for('index'))

        stream_record = {
            "name": stream_name,
            "url": stream_url,
            "added_at": datetime.utcnow()
        }
        stream_configs_collection.insert_one(stream_record)
        flash(f"Stream '{stream_name}' added successfully. Restart anpr-service to apply changes.", 'success')
        app.logger.info(f"Stream config added: {stream_name} - {stream_url}")
    except OperationFailure as e:
        app.logger.error(f"MongoDB error when adding stream: {e}")
        flash('Error adding stream (database error).', 'error')
    except Exception as e:
        app.logger.error(f"Error adding stream: {e}", exc_info=True)
        flash(f"An unexpected error occurred while adding stream: {e}", 'error')
    
    return redirect(url_for('index'))


@app.route('/delete_stream/<stream_id>', methods=['POST'])
def delete_stream(stream_id):
    if stream_configs_collection is None:
        flash('Database service not available for managing streams.', 'error')
        return redirect(url_for('index'))

    try:
        result = stream_configs_collection.delete_one({'_id': ObjectId(stream_id)})
        if result.deleted_count == 1:
            flash('Stream deleted successfully. Restart anpr-service to apply changes.', 'success')
            app.logger.info(f"Stream config deleted: {stream_id}")
        else:
            flash('Stream not found or already deleted.', 'error')
            app.logger.warning(f"Attempted to delete non-existent stream config: {stream_id}")
    except OperationFailure as e:
        app.logger.error(f"MongoDB error when deleting stream: {e}")
        flash('Error deleting stream (database error).', 'error')
    except Exception as e: # Catches InvalidId from ObjectId conversion too
        app.logger.error(f"Error deleting stream {stream_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred while deleting stream: {e}", 'error')

    return redirect(url_for('index'))


if __name__ == '__main__':
    # This is for local development without Docker,
    # Docker execution uses `flask run` command from Dockerfile CMD
    # Ensure UPLOAD_FOLDER is created for local dev
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try:
            os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
        except OSError:
            pass # ignore error if it already exists due to race condition
    app.run(host='0.0.0.0', port=5000, debug=True)
