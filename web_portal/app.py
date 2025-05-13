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
PARKING_SESSIONS_COLLECTION_NAME = 'parking_sessions' # For displaying parking sessions

# Path for web-portal to save uploads. This is on the anpr_images volume.
UPLOAD_SUBDIR = "uploads" # Subdirectory within the shared volume anpr_images
PATH_FOR_WEBPORTAL_SAVE = os.path.join("/app/static/detections", UPLOAD_SUBDIR)
PATH_FOR_ANPR_SERVICE_READ_PREFIX = os.path.join("/app/detections", UPLOAD_SUBDIR)


ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}

client = None
db = None
detections_collection = None
video_jobs_collection = None
stream_configs_collection = None 
parking_sessions_collection = None # For displaying parking sessions

try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ismaster')
    db = client[DB_NAME]
    detections_collection = db[DETECTIONS_COLLECTION_NAME]
    video_jobs_collection = db[VIDEO_JOBS_COLLECTION_NAME]
    stream_configs_collection = db[STREAM_CONFIGS_COLLECTION_NAME]
    parking_sessions_collection = db[PARKING_SESSIONS_COLLECTION_NAME] # Initialize parking sessions collection
    app.logger.info("Successfully connected to MongoDB.")

    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try:
            os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
            app.logger.info(f"Created upload directory: {PATH_FOR_WEBPORTAL_SAVE}")
        except OSError as e:
            app.logger.error(f"Could not create upload directory {PATH_FOR_WEBPORTAL_SAVE}: {e}")

except ConnectionFailure:
    app.logger.error("MongoDB connection failed.")
except Exception as e:
    app.logger.error(f"An error occurred during MongoDB initialization: {e}")


def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
def index():
    detections = [] # For raw detections log
    stream_configs = []
    parking_sessions = [] # For parking sessions display
    error_message = None

    # Fetch raw detections (optional display based on template)
    if detections_collection is not None:
        try:
            detections_cursor = detections_collection.find().sort("timestamp", -1).limit(50) 
            for detection_doc in detections_cursor: 
                if 'image_path' in detection_doc and detection_doc['image_path']:
                    if detection_doc['image_path'].startswith('/'):
                        detection_doc['image_url'] = url_for('static', filename=detection_doc['image_path'][1:])
                    else:
                        detection_doc['image_url'] = url_for('static', filename=detection_doc['image_path'])
                else:
                    detection_doc['image_url'] = None
                detections.append(detection_doc)
        except Exception as e:
            app.logger.error(f"Error fetching raw detections from MongoDB: {e}")
            error_message = "Error fetching raw detection data."
    else:
        if not error_message: error_message = "DB connection not available for raw detections."
        else: error_message += " DB connection also not available for raw detections."
        app.logger.warning("Attempted to fetch raw detections, but DB connection is not available.")

    # Fetch parking sessions
    if parking_sessions_collection is not None:
        try:
            sessions_cursor = parking_sessions_collection.find().sort("entry_timestamp", -1).limit(50) # Sort by entry, limit
            for session in sessions_cursor:
                # Prepare image URLs for entry image
                if 'entry_image_path' in session and session['entry_image_path']:
                    if session['entry_image_path'].startswith('/'):
                        session['entry_image_url'] = url_for('static', filename=session['entry_image_path'][1:])
                    else:
                        session['entry_image_url'] = url_for('static', filename=session['entry_image_path'])
                else:
                    session['entry_image_url'] = None
                
                # Prepare image URLs for exit image
                if 'exit_image_path' in session and session['exit_image_path']:
                    if session['exit_image_path'].startswith('/'):
                        session['exit_image_url'] = url_for('static', filename=session['exit_image_path'][1:])
                    else:
                        session['exit_image_url'] = url_for('static', filename=session['exit_image_path'])
                else:
                    session['exit_image_url'] = None

                # Calculate duration if exited
                if session.get('status') == 'exited' and session.get('entry_timestamp') and session.get('exit_timestamp'):
                    duration = session['exit_timestamp'] - session['entry_timestamp']
                    session['duration_str'] = str(duration).split('.')[0] # Remove microseconds for display
                else:
                    session['duration_str'] = "N/A" # Simpler message
                parking_sessions.append(session)
        except Exception as e:
            app.logger.error(f"Error fetching parking sessions from MongoDB: {e}", exc_info=True)
            if not error_message: error_message = "Error fetching parking session data."
            else: error_message += " Also, error fetching parking session data."
    else:
        if not error_message: error_message = "DB connection not available for parking sessions."
        else: error_message += " DB connection also not available for parking sessions."
        app.logger.warning("Attempted to fetch parking sessions, but DB connection is not available.")

    # Fetch stream configurations (existing logic)
    if stream_configs_collection is not None:
        try:
            stream_configs = list(stream_configs_collection.find().sort("name", 1))
        except Exception as e:
            app.logger.error(f"Error fetching stream configs: {e}")
            if not error_message: 
                 error_message = "Error fetching stream configurations."
            else:
                error_message += " Also, error fetching stream configurations."
    elif not error_message: 
        error_message = "Database connection not available for stream configurations."
        app.logger.warning("Attempted to fetch stream configs, but DB connection is not available.")
    else: 
        error_message += " Also, database connection not available for stream configurations."

    return render_template('index.html', detections=detections, stream_configs=stream_configs, parking_sessions=parking_sessions, error_message=error_message)


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
        unique_suffix = uuid.uuid4().hex[:8]
        filename = f"{unique_suffix}_{original_filename}"
        
        try:
            if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
                 os.makedirs(PATH_FOR_WEBPORTAL_SAVE)

            save_path = os.path.join(PATH_FOR_WEBPORTAL_SAVE, filename)
            file.save(save_path)
            app.logger.info(f"Video saved to {save_path}")

            anpr_service_video_path = os.path.join(PATH_FOR_ANPR_SERVICE_READ_PREFIX, filename)

            job_id = uuid.uuid4().hex
            job_record = {
                "job_id": job_id, "video_path": anpr_service_video_path,
                "original_filename": original_filename, "status": "pending",
                "uploaded_at": datetime.utcnow(), "processed_at": None,
                "anpr_service_messages": []
            }
            video_jobs_collection.insert_one(job_record)
            flash(f"Video '{original_filename}' uploaded successfully and queued for processing (Job ID: {job_id}).", 'success')
            app.logger.info(f"Video job {job_id} for {anpr_service_video_path} added to MongoDB.")

        except FileNotFoundError: 
            app.logger.error(f"Upload directory {PATH_FOR_WEBPORTAL_SAVE} does not exist. Cannot save file.")
            flash(f"Error: Upload directory not found on server.", 'error')
        except OperationFailure as e:
            app.logger.error(f"MongoDB error when adding video job: {e}")
            flash('Error queuing video for processing (database error).', 'error')
        except Exception as e:
            app.logger.error(f"Error uploading video: {e}", exc_info=True)
            flash(f"An unexpected error occurred: {e}", 'error')
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
    stream_role = request.form.get('stream_role')

    allowed_roles = ["common", "entry", "exit", "monitoring"]
    if not stream_name or not stream_url or not stream_role:
        flash('Stream name, URL, and role are required.', 'error')
        return redirect(url_for('index'))

    if stream_role not in allowed_roles:
        flash(f"Invalid stream role '{stream_role}'. Allowed roles are: {', '.join(allowed_roles)}.", 'error')
        return redirect(url_for('index'))

    try:
        existing_stream = stream_configs_collection.find_one({"name": stream_name})
        if existing_stream:
            flash(f"A stream with the name '{stream_name}' already exists. Please use a unique name.", 'error')
            return redirect(url_for('index'))

        stream_record = {
            "name": stream_name, "url": stream_url,
            "role": stream_role, "added_at": datetime.utcnow()
        }
        stream_configs_collection.insert_one(stream_record)
        flash(f"Stream '{stream_name}' (Role: {stream_role}) added successfully. Restart anpr-service to apply changes.", 'success')
        app.logger.info(f"Stream config added: {stream_name} (Role: {stream_role}) - {stream_url}")
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
    except Exception as e: 
        app.logger.error(f"Error deleting stream {stream_id}: {e}", exc_info=True)
        flash(f"An unexpected error occurred while deleting stream: {e}", 'error')

    return redirect(url_for('index'))


if __name__ == '__main__':
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try:
            os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
        except OSError:
            pass 
    app.run(host='0.0.0.0', port=5000, debug=True)
