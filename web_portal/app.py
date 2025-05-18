import os
import uuid
from flask import Flask, render_template, url_for, request, redirect, flash 
from werkzeug.utils import secure_filename
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from bson.objectid import ObjectId
from datetime import datetime
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_bcrypt import Bcrypt
from functools import wraps
import requests 
import pytz # Added for timezone handling

app = Flask(__name__)

# Configuration
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_key_for_dev_only_change_me')
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'anpr_db'
DEFAULT_OPERATIONAL_TIMEZONE = "UTC" # Fallback timezone

DETECTIONS_COLLECTION_NAME = 'detections'
VIDEO_JOBS_COLLECTION_NAME = 'video_jobs'
STREAM_CONFIGS_COLLECTION_NAME = 'stream_configs'
PARKING_SESSIONS_COLLECTION_NAME = 'parking_sessions'
USERS_COLLECTION_NAME = 'users'

UPLOAD_SUBDIR = "uploads"
PATH_FOR_WEBPORTAL_SAVE = os.path.join("/app/static/detections", UPLOAD_SUBDIR)
PATH_FOR_ANPR_SERVICE_READ_PREFIX = os.path.join("/app/detections", UPLOAD_SUBDIR)
ALLOWED_EXTENSIONS = {'mp4', 'avi', 'mov', 'mkv'}
VALID_APP_USER_ROLES = ['admin', 'operator', 'supervisor', 'technical']

VEHICLE_TYPES_SUPPORTED_FOR_UI = {
    "CAR_SUV": "Car/SUV",
    "TRUCK": "Truck/Van",
    "MOTORCYCLE": "Motorcycle"
}

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
bcrypt = Bcrypt(app)

client = None
db = None
detections_collection, video_jobs_collection, stream_configs_collection, parking_sessions_collection, users_collection = None, None, None, None, None

def create_initial_admin_user():
    app.logger.info("Attempting to create initial admin user...")
    if users_collection is None:
        app.logger.warning("users_collection is None, cannot create initial admin user.")
        return
    try:
        if users_collection.count_documents({}) == 0:
            app.logger.info("Users collection is empty, proceeding to create admin user.")
            hashed_password = bcrypt.generate_password_hash("admin").decode('utf-8')
            users_collection.insert_one({"username": "admin", "password_hash": hashed_password, "roles": ["admin", "operator"]})
            app.logger.info("Created initial admin user (username 'admin', password 'admin'). Please change this password.")
        else:
            app.logger.info("Users collection is not empty. Initial admin user not created.")
    except Exception as e:
        app.logger.error(f"Failed to create or check for initial admin user: {e}", exc_info=True)

try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ismaster')
    db = client[DB_NAME]
    detections_collection = db[DETECTIONS_COLLECTION_NAME]
    video_jobs_collection = db[VIDEO_JOBS_COLLECTION_NAME]
    stream_configs_collection = db[STREAM_CONFIGS_COLLECTION_NAME]
    parking_sessions_collection = db[PARKING_SESSIONS_COLLECTION_NAME]
    users_collection = db[USERS_COLLECTION_NAME]
    app.logger.info("Successfully connected to MongoDB.")
    if users_collection is not None: create_initial_admin_user()
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try: os.makedirs(PATH_FOR_WEBPORTAL_SAVE); app.logger.info(f"Created upload directory: {PATH_FOR_WEBPORTAL_SAVE}")
        except OSError as e: app.logger.error(f"Could not create upload directory {PATH_FOR_WEBPORTAL_SAVE}: {e}")
except ConnectionFailure: app.logger.error("MongoDB connection failed.")
except Exception as e: app.logger.error(f"An error occurred during MongoDB initialization: {e}")

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data["_id"])
        self.username = user_data["username"]
        self.password_hash = user_data["password_hash"]
        self.roles = user_data.get("roles", [])
    def check_password(self, password): return bcrypt.check_password_hash(self.password_hash, password)
    def has_role(self, role): return role in self.roles
    def has_any_role(self, roles_to_check): return any(self.has_role(r) for r in roles_to_check)

@login_manager.user_loader
def load_user(user_id):
    if users_collection is None: return None
    user_data = users_collection.find_one({"_id": ObjectId(user_id)})
    return User(user_data) if user_data else None

def roles_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not current_user.is_authenticated: return login_manager.unauthorized()
            if not current_user.has_any_role(allowed_roles):
                flash(f"Access denied. Required roles: {', '.join(allowed_roles)}.", 'error')
                return redirect(url_for('index'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated: return redirect(url_for('index'))
    if request.method == 'POST':
        if users_collection is None: flash("Database service not available.", "error"); return render_template('login.html')
        username = request.form.get('username'); password = request.form.get('password')
        user_data = users_collection.find_one({"username": username})
        if user_data and User(user_data).check_password(password):
            login_user(User(user_data)); flash('Logged in successfully.', 'success'); return redirect(url_for('index'))
        else: flash('Invalid username or password.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout(): logout_user(); flash('You have been logged out.', 'success'); return redirect(url_for('login'))

def allowed_file(filename): return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/')
@login_required
def index():
    # Remove raw detections fetching from index, it's on its own page now
    stream_configs, parking_sessions, error_message = [], [], None
    plate_query_sessions = request.args.get('plate_query_sessions', '').strip()
    app.logger.debug(f"Index route: plate_query_sessions = '{plate_query_sessions}'")

    if client is None: error_message = "MongoDB connection failed. Data cannot be loaded."
    else:
        # Raw detections are no longer fetched here
        # try:
        #     if detections_collection is not None:
        #         detections_cursor = detections_collection.find().sort("timestamp", -1).limit(50)
        #         for doc in detections_cursor:
        #             entry_path = doc.get('image_path')
        #             if entry_path and isinstance(entry_path, str):
        #                 doc['image_url'] = url_for('static', filename=entry_path[1:] if entry_path.startswith('/') else entry_path)
        #             detections.append(doc)
        # except Exception as e: app.logger.error(f"Error fetching raw detections: {e}"); error_message = (error_message or "") + "Error fetching raw detections."
        
        try:
            if parking_sessions_collection is not None:
                session_filter = {}
                if plate_query_sessions:
                    session_filter['plate_number'] = {"$regex": plate_query_sessions, "$options": "i"}
                app.logger.debug(f"Index route: Applying session_filter = {session_filter}")
                
                sessions_cursor = parking_sessions_collection.find(session_filter).sort("entry_timestamp", -1).limit(50)
                for s in sessions_cursor:
                    entry_path = s.get('entry_image_path')
                    if entry_path and isinstance(entry_path, str): s['entry_image_url'] = url_for('static', filename=entry_path[1:] if entry_path.startswith('/') else entry_path)
                    exit_path = s.get('exit_image_path')
                    if exit_path and isinstance(exit_path, str): s['exit_image_url'] = url_for('static', filename=exit_path[1:] if exit_path.startswith('/') else exit_path)
                    if s.get('status') == 'exited' and s.get('entry_timestamp') and s.get('exit_timestamp'): s['duration_str'] = str(s['exit_timestamp'] - s['entry_timestamp']).split('.')[0]
                    else: s['duration_str'] = "N/A"
                    parking_sessions.append(s)
                
                if plate_query_sessions and not parking_sessions:
                     flash(f"No parking sessions found matching plate '{plate_query_sessions}'.", "info")

        except Exception as e: app.logger.error(f"Error fetching parking sessions: {e}", exc_info=True); error_message = (error_message or "") + "Error fetching parking sessions."
        
        try:
            if stream_configs_collection is not None: stream_configs = list(stream_configs_collection.find().sort("name", 1))
        except Exception as e: app.logger.error(f"Error fetching stream configs: {e}"); error_message = (error_message or "") + "Error fetching stream configs."
            
    return render_template('index.html', 
                           stream_configs=stream_configs, 
                           parking_sessions=parking_sessions, 
                           error_message=error_message, 
                           current_user=current_user,
                           plate_query_sessions=plate_query_sessions) # Pass query back

@app.route('/upload', methods=['POST'])
@login_required
@roles_required(['admin', 'technical', 'supervisor'])
def upload_video():
    if video_jobs_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    if 'video_file' not in request.files: flash('No video file part.', 'error'); return redirect(url_for('index'))
    file = request.files['video_file']
    if file.filename == '': flash('No video selected.', 'error'); return redirect(url_for('index'))
    if file and allowed_file(file.filename):
        original_filename = secure_filename(file.filename)
        unique_suffix = uuid.uuid4().hex[:8]; filename = f"{unique_suffix}_{original_filename}"
        try:
            if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE): os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
            save_path = os.path.join(PATH_FOR_WEBPORTAL_SAVE, filename); file.save(save_path)
            anpr_service_video_path = os.path.join(PATH_FOR_ANPR_SERVICE_READ_PREFIX, filename)
            job_id = uuid.uuid4().hex
            job_record = {"job_id": job_id, "video_path": anpr_service_video_path, "original_filename": original_filename, "status": "pending", "uploaded_at": datetime.utcnow()}
            video_jobs_collection.insert_one(job_record)
            flash(f"Video '{original_filename}' uploaded (Job ID: {job_id}).", 'success')
        except Exception as e:
            app.logger.error(f"Error uploading video: {e}", exc_info=True); flash(f"Error: {e}", 'error')
            if 'save_path' in locals() and os.path.exists(save_path):
                try: os.remove(save_path)
                except OSError as rm_e: app.logger.error(f"Error cleaning file {save_path}: {rm_e}")
    else: flash('Invalid file type. Allowed: ' + ", ".join(ALLOWED_EXTENSIONS), 'error')
    return redirect(url_for('index'))

@app.route('/add_stream', methods=['POST'])
@login_required
@roles_required(['admin', 'technical'])
def add_stream():
    if stream_configs_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    stream_name = request.form.get('stream_name'); stream_url = request.form.get('stream_url'); stream_camera_role = request.form.get('stream_role')
    if not (stream_name and stream_url and stream_camera_role and stream_camera_role in ["common", "entry", "exit", "monitoring"]):
        flash('Stream name, URL, and valid camera role are required.', 'error'); return redirect(url_for('index'))
    try:
        if stream_configs_collection.find_one({"name": stream_name}): flash(f"Stream name '{stream_name}' already exists.", 'error'); return redirect(url_for('index'))
        stream_configs_collection.insert_one({"name": stream_name, "url": stream_url, "role": stream_camera_role, "added_at": datetime.utcnow()})
        flash(f"Stream '{stream_name}' (Role: {stream_camera_role}) added. Restart anpr-service.", 'success')
    except Exception as e: app.logger.error(f"Error adding stream: {e}", exc_info=True); flash('Error adding stream.', 'error')
    return redirect(url_for('index'))

@app.route('/delete_stream/<stream_id>', methods=['POST'])
@login_required
@roles_required(['admin', 'technical'])
def delete_stream(stream_id):
    if stream_configs_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    try:
        result = stream_configs_collection.delete_one({'_id': ObjectId(stream_id)})
        if result.deleted_count == 1: flash('Stream deleted. Restart anpr-service.', 'success')
        else: flash('Stream not found.', 'error')
    except Exception as e: app.logger.error(f"Error deleting stream {stream_id}: {e}", exc_info=True); flash('Error deleting stream.', 'error')
    return redirect(url_for('index'))

def get_configured_timezone_str():
    try:
        response = requests.get('http://cashier-service:5001/api/settings/timezone', timeout=2)
        if response.status_code == 200:
            return response.json().get('timezone', DEFAULT_OPERATIONAL_TIMEZONE)
    except requests.exceptions.RequestException:
        app.logger.warning("Could not fetch timezone from cashier-service, defaulting to UTC.")
    return DEFAULT_OPERATIONAL_TIMEZONE

# Custom Jinja2 filter for local time display
def format_datetime_local(utc_dt, format_str='%Y-%m-%d %H:%M:%S'): # Renamed format to format_str
    if not utc_dt:
        return 'N/A'
    
    # Log initial state of utc_dt
    # app.logger.debug(f"format_datetime_local: Received utc_dt = {utc_dt}, type = {type(utc_dt)}, tzinfo = {utc_dt.tzinfo if hasattr(utc_dt, 'tzinfo') else 'N/A'}")

    try:
        original_utc_dt_repr = repr(utc_dt) # For logging before modification

        if not isinstance(utc_dt, datetime):
            app.logger.warning(f"format_datetime_local: Received non-datetime object: {utc_dt} (type: {type(utc_dt)})")
            return str(utc_dt) # Or some error string

        # Ensure utc_dt is timezone-aware and set to UTC
        if utc_dt.tzinfo is None:
            utc_dt_aware = pytz.utc.localize(utc_dt)
            # app.logger.debug(f"format_datetime_local: Localized naive dt {original_utc_dt_repr} to aware UTC: {utc_dt_aware}")
        else:
            utc_dt_aware = utc_dt.astimezone(pytz.utc)
            # if utc_dt_aware != utc_dt: # Log if conversion happened
                # app.logger.debug(f"format_datetime_local: Converted aware dt {original_utc_dt_repr} to UTC: {utc_dt_aware}")

        target_tz_str = get_configured_timezone_str() 
        # app.logger.debug(f"format_datetime_local: Target timezone string from helper: '{target_tz_str}'")
        
        target_tz = pytz.timezone(target_tz_str)
        local_dt = utc_dt_aware.astimezone(target_tz)
        
        # app.logger.debug(f"format_datetime_local: Converted {utc_dt_aware} to local_dt {local_dt} ({target_tz_str})")
        
        return local_dt.strftime(format_str)
    except Exception as e:
        app.logger.error(f"Error formatting datetime '{original_utc_dt_repr if 'original_utc_dt_repr' in locals() else utc_dt}' to local: {e}", exc_info=True)
        # Fallback to UTC display if conversion fails, indicating it's UTC. Ensure utc_dt is a datetime object for strftime.
        if isinstance(utc_dt, datetime):
            return utc_dt.strftime(format_str) + " (UTC)"
        return str(utc_dt) + " (Error - UTC)"


app.jinja_env.filters['datetime_local'] = format_datetime_local

@app.route('/edit_session/<session_id>', methods=['GET', 'POST'])
@login_required
@roles_required(['admin', 'supervisor'])
def edit_session(session_id):
    if parking_sessions_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    try: session_obj_id = ObjectId(session_id)
    except Exception: flash('Invalid session ID.', 'error'); return redirect(url_for('index'))
    session_data = parking_sessions_collection.find_one({'_id': session_obj_id})
    if not session_data: flash('Parking session not found.', 'error'); return redirect(url_for('index'))
    
    session_for_template = dict(session_data) 
    if session_for_template.get('entry_image_path'): session_for_template['entry_image_url'] = url_for('static', filename=session_for_template['entry_image_path'][1:] if session_for_template['entry_image_path'].startswith('/') else session_for_template['entry_image_path'])
    if session_for_template.get('exit_image_path'): session_for_template['exit_image_url'] = url_for('static', filename=session_for_template['exit_image_path'][1:] if session_for_template['exit_image_path'].startswith('/') else session_for_template['exit_image_path'])
        
    if request.method == 'POST':
        new_status = request.form.get('status'); new_exit_timestamp_str = request.form.get('exit_timestamp')
        new_vehicle_type = request.form.get('vehicle_type')
        update_fields = {}

        if new_status in ['inside', 'exited']: update_fields['status'] = new_status
        else: flash('Invalid status.', 'error'); return render_template('edit_session.html', session=session_for_template, vehicle_types=VEHICLE_TYPES_SUPPORTED_FOR_UI)
        
        if new_vehicle_type and new_vehicle_type in VEHICLE_TYPES_SUPPORTED_FOR_UI: update_fields['vehicle_type'] = new_vehicle_type
        elif new_vehicle_type: flash(f"Invalid vehicle type '{new_vehicle_type}'.", 'error'); return render_template('edit_session.html', session=session_for_template, vehicle_types=VEHICLE_TYPES_SUPPORTED_FOR_UI)

        if new_exit_timestamp_str:
            try:
                naive_exit_dt = datetime.strptime(new_exit_timestamp_str, '%Y-%m-%dT%H:%M')
                configured_tz_str = get_configured_timezone_str()
                local_tz = pytz.timezone(configured_tz_str)
                aware_local_dt = local_tz.localize(naive_exit_dt)
                utc_exit_dt = aware_local_dt.astimezone(pytz.utc)
                update_fields['exit_timestamp'] = utc_exit_dt
                app.logger.info(f"Converted local exit time {new_exit_timestamp_str} (as {configured_tz_str}) to UTC {utc_exit_dt}")
            except ValueError: flash('Invalid exit timestamp format.', 'error'); return render_template('edit_session.html', session=session_for_template, vehicle_types=VEHICLE_TYPES_SUPPORTED_FOR_UI)
            except pytz.exceptions.UnknownTimeZoneError:
                flash(f"Server configuration error: Unknown timezone '{configured_tz_str}'. Using UTC for exit time.", 'error')
                update_fields['exit_timestamp'] = naive_exit_dt # Fallback
        elif new_status == 'exited': # Exit time required if status is 'exited' and no timestamp provided
            flash('Exit timestamp required for "exited" status.', 'error'); return render_template('edit_session.html', session=session_for_template, vehicle_types=VEHICLE_TYPES_SUPPORTED_FOR_UI)
        
        if new_status == 'inside': 
            update_fields.update({'exit_timestamp': None, 'exit_image_path': None, 'exit_camera_id': None, 
                                  'payment_status': "unpaid", 'calculated_cost': None, 'amount_received': None, 
                                  'change_given': None, 'payment_timestamp': None})

        if update_fields:
            try:
                parking_sessions_collection.update_one({'_id': session_obj_id}, {'$set': update_fields})
                flash('Parking session updated.', 'success'); return redirect(url_for('index'))
            except Exception as e: app.logger.error(f"Error updating session {session_id}: {e}", exc_info=True); flash('Error updating session.', 'error')
        else: flash('No changes submitted.', 'info')
        
        session_data_updated_post = parking_sessions_collection.find_one({'_id': session_obj_id}) 
        if session_data_updated_post: session_for_template = dict(session_data_updated_post) # Re-assign for re-render
        # Re-prepare image URLs if needed for re-render
        if session_for_template.get('entry_image_path'): session_for_template['entry_image_url'] = url_for('static', filename=session_for_template['entry_image_path'][1:] if session_for_template['entry_image_path'].startswith('/') else session_for_template['entry_image_path'])
        if session_for_template.get('exit_image_path'): session_for_template['exit_image_url'] = url_for('static', filename=session_for_template['exit_image_path'][1:] if session_for_template['exit_image_path'].startswith('/') else session_for_template['exit_image_path'])

    return render_template('edit_session.html', session=session_for_template, vehicle_types=VEHICLE_TYPES_SUPPORTED_FOR_UI)

@app.route('/users', methods=['GET'])
@login_required
@roles_required(['admin', 'supervisor']) 
def manage_users():
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    all_users = [dict(u, _id=str(u['_id'])) for u in users_collection.find()]
    admin_users_count = sum(1 for u in all_users if 'admin' in u.get('roles', []))
    return render_template('manage_users.html', users=all_users, admin_users_count=admin_users_count)

@app.route('/users/add', methods=['POST'])
@login_required
@roles_required(['admin']) 
def add_user():
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('manage_users'))
    username = request.form.get('username'); password = request.form.get('password'); roles = request.form.getlist('roles')
    if not (username and password): flash('Username and password required.', 'error'); return redirect(url_for('manage_users'))
    if any(r not in VALID_APP_USER_ROLES for r in roles): flash("Invalid role selected.", 'error'); return redirect(url_for('manage_users'))
    if not roles: roles = ['operator']; flash('No roles selected, defaulting to "operator".', 'info')
    if users_collection.find_one({"username": username}): flash(f"Username '{username}' already exists.", 'error'); return redirect(url_for('manage_users'))
    try:
        users_collection.insert_one({"username": username, "password_hash": bcrypt.generate_password_hash(password).decode('utf-8'), "roles": roles})
        flash(f"User '{username}' created.", 'success')
    except Exception as e: app.logger.error(f"Error creating user '{username}': {e}", exc_info=True); flash('Error creating user.', 'error')
    return redirect(url_for('manage_users'))

@app.route('/users/edit/<user_id>', methods=['GET', 'POST'])
@login_required
@roles_required(['admin', 'supervisor'])
def edit_user(user_id):
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('manage_users'))
    try: user_obj_id = ObjectId(user_id)
    except Exception: flash('Invalid user ID.', 'error'); return redirect(url_for('manage_users'))
    user_data_db = users_collection.find_one({'_id': user_obj_id}) 
    if not user_data_db: flash('User not found.', 'error'); return redirect(url_for('manage_users'))
    
    user_for_template = dict(user_data_db, roles=user_data_db.get('roles', []))

    if request.method == 'POST':
        new_password = request.form.get('password'); confirm_password = request.form.get('confirm_password')
        new_roles = request.form.getlist('roles')
        update_fields = {}

        if current_user.has_role('supervisor') and not current_user.has_role('admin'):
            if user_data_db['username'] == 'admin': flash("Supervisors cannot edit the primary 'admin' user.", 'error'); return redirect(url_for('manage_users'))
            if 'admin' in new_roles: new_roles = [r for r in new_roles if r != 'admin']; flash("Supervisors cannot grant 'admin' role. Admin role removed.", 'warning')
        
        if new_password:
            if new_password != confirm_password: flash('Passwords do not match.', 'error'); return render_template('edit_user.html', user=user_for_template)
            if len(new_password) < 4: flash('Password must be at least 4 characters.', 'error'); return render_template('edit_user.html', user=user_for_template)
            update_fields['password_hash'] = bcrypt.generate_password_hash(new_password).decode('utf-8')

        if any(r not in VALID_APP_USER_ROLES for r in new_roles): flash("Invalid role selected.", 'error'); return render_template('edit_user.html', user=user_for_template)
        
        is_editing_self = current_user.id == user_id
        if 'admin' not in new_roles and 'admin' in user_data_db.get('roles',[]) and (is_editing_self or user_data_db['username'] == 'admin'):
            if users_collection.count_documents({"roles": "admin", "_id": {"$ne": user_obj_id}}) == 0:
                flash("Cannot remove 'admin' role, would leave no administrators.", 'error'); return render_template('edit_user.html', user=user_for_template)
        
        update_fields['roles'] = new_roles if new_roles else ['operator']

        if update_fields:
            try:
                users_collection.update_one({'_id': user_obj_id}, {'$set': update_fields})
                flash(f"User '{user_data_db['username']}' updated.", 'success'); return redirect(url_for('manage_users'))
            except Exception as e: app.logger.error(f"Error updating user {user_id}: {e}", exc_info=True); flash('Error updating user.', 'error')
        else: flash('No changes submitted.', 'info')
        
        user_data_updated_post = users_collection.find_one({'_id': user_obj_id}); user_for_template = dict(user_data_updated_post, roles=user_data_updated_post.get('roles', []))
    return render_template('edit_user.html', user=user_for_template)

@app.route('/users/delete/<user_id>', methods=['POST'])
@login_required
@roles_required(['admin']) 
def delete_user(user_id):
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('manage_users'))
    try:
        user_to_delete = users_collection.find_one({"_id": ObjectId(user_id)})
        if not user_to_delete: flash("User not found.", "error"); return redirect(url_for('manage_users'))
        if current_user.id == user_id: flash("You cannot delete yourself.", 'error'); return redirect(url_for('manage_users'))
        if user_to_delete['username'] == 'admin' and users_collection.count_documents({"roles": "admin", "username": {"$ne": "admin"}}) == 0:
            flash("Cannot delete primary 'admin' if it's the only admin.", 'error'); return redirect(url_for('manage_users'))
        users_collection.delete_one({"_id": ObjectId(user_id)})
        flash(f"User '{user_to_delete['username']}' deleted.", 'success')
    except Exception as e: app.logger.error(f"Error deleting user {user_id}: {e}", exc_info=True); flash('Error deleting user.', 'error')
    return redirect(url_for('manage_users'))

# --- Admin Settings Routes ---
@app.route('/admin/settings', methods=['GET'])
@login_required
@roles_required(['admin'])
def admin_settings():
    current_inflation_factor = 1.0 
    inflation_last_updated = "N/A"
    current_operational_timezone = DEFAULT_OPERATIONAL_TIMEZONE
    operational_timezone_last_updated = "N/A"
    all_rates_data = {}

    try:
        response_inflation = requests.get('http://cashier-service:5001/api/inflation-factor', timeout=5)
        if response_inflation.status_code == 200:
            data_inflation = response_inflation.json()
            current_inflation_factor = data_inflation.get('factor', 1.0)
            last_updated_ts_inflation = data_inflation.get('last_updated')
            if last_updated_ts_inflation:
                try:
                    dt_obj = datetime.fromisoformat(last_updated_ts_inflation.replace("Z", "+00:00")) if isinstance(last_updated_ts_inflation, str) else datetime.fromtimestamp(last_updated_ts_inflation) if isinstance(last_updated_ts_inflation, (int, float)) else None
                    if dt_obj: inflation_last_updated = dt_obj.strftime('%Y-%m-%d %H:%M:%S UTC')
                except Exception: inflation_last_updated = str(last_updated_ts_inflation)
        else: flash(f"Error fetching inflation factor: {response_inflation.status_code}", "error")
    except requests.exceptions.RequestException: flash("Could not load inflation factor: cashier service unavailable.", "warning")

    try:
        response_tz = requests.get('http://cashier-service:5001/api/settings/timezone', timeout=5)
        if response_tz.status_code == 200:
            data_tz = response_tz.json()
            current_operational_timezone = data_tz.get('timezone', DEFAULT_OPERATIONAL_TIMEZONE)
            last_updated_ts_tz = data_tz.get('last_updated')
            if last_updated_ts_tz:
                try:
                    dt_obj_tz = datetime.fromisoformat(last_updated_ts_tz.replace("Z", "+00:00")) if isinstance(last_updated_ts_tz, str) else datetime.fromtimestamp(last_updated_ts_tz) if isinstance(last_updated_ts_tz, (int, float)) else None
                    if dt_obj_tz: operational_timezone_last_updated = dt_obj_tz.strftime('%Y-%m-%d %H:%M:%S UTC')
                except Exception: operational_timezone_last_updated = str(last_updated_ts_tz)
        else: flash(f"Error fetching operational timezone: {response_tz.status_code}", "error")
    except requests.exceptions.RequestException: flash("Could not load operational timezone: cashier service unavailable.", "warning")

    try:
        response_rates = requests.get('http://cashier-service:5001/api/rates', timeout=5)
        if response_rates.status_code == 200: all_rates_data = response_rates.json()
        else: flash(f"Error fetching base rates: {response_rates.status_code}", "error")
    except requests.exceptions.RequestException: flash("Could not load base rates: cashier service unavailable.", "warning")
        
    return render_template('admin_settings.html', 
                           current_inflation_factor=current_inflation_factor, 
                           inflation_last_updated=inflation_last_updated,
                           all_rates=all_rates_data,
                           current_operational_timezone=current_operational_timezone,
                           operational_timezone_last_updated=operational_timezone_last_updated)

@app.route('/admin/update_inflation_factor', methods=['POST'])
@login_required
@roles_required(['admin'])
def update_inflation_factor():
    new_factor_str = request.form.get('inflation_factor')
    try:
        new_factor = float(new_factor_str)
        if new_factor <= 0: flash("Inflation factor must be a positive number.", "error")
        else:
            try:
                response = requests.post('http://cashier-service:5001/api/inflation-factor', json={'factor': new_factor}, timeout=5)
                if response.status_code == 200: flash("Inflation factor updated successfully.", "success")
                else: flash(f"Failed to update inflation factor: {response.json().get('error', response.text)}", "error")
            except requests.exceptions.RequestException: flash("Could not update inflation factor: cashier service unavailable.", "error")
    except ValueError: flash("Invalid number format for inflation factor.", "error")
    except Exception as e: app.logger.error(f"Error updating inflation factor: {e}", exc_info=True); flash("Unexpected error.", "error")
    return redirect(url_for('admin_settings'))

@app.route('/admin/update_operational_timezone', methods=['POST'])
@login_required
@roles_required(['admin'])
def update_operational_timezone():
    new_timezone_str = request.form.get('operational_timezone')
    if not new_timezone_str: flash("Timezone value cannot be empty.", "error"); return redirect(url_for('admin_settings'))
    try:
        pytz.timezone(new_timezone_str) 
    except pytz.exceptions.UnknownTimeZoneError: flash(f"Invalid timezone: '{new_timezone_str}'.", "error"); return redirect(url_for('admin_settings'))
    except Exception as e_tz_val: flash(f"Error validating timezone '{new_timezone_str}': {e_tz_val}", "error"); return redirect(url_for('admin_settings'))

    try:
        response = requests.post('http://cashier-service:5001/api/settings/timezone', json={'timezone': new_timezone_str}, timeout=5)
        if response.status_code == 200: flash("Operational timezone updated.", "success")
        else: flash(f"Failed to update timezone: {response.json().get('error', response.text)}", "error")
    except requests.exceptions.RequestException: flash("Could not update timezone: cashier service unavailable.", "error")
    except Exception as e: app.logger.error(f"Error updating timezone: {e}", exc_info=True); flash("Unexpected error.", "error")
    return redirect(url_for('admin_settings'))

@app.route('/admin/update_base_rates', methods=['POST'])
@login_required
@roles_required(['admin'])
def update_base_rates():
    try:
        vehicle_types_from_form = {request.form[key] for key in request.form if key.endswith("_vehicle_type_code")}
        for vt_code in vehicle_types_from_form:
            if vt_code not in VEHICLE_TYPES_SUPPORTED_FOR_UI: app.logger.warning(f"Skipping unknown vehicle type: {vt_code}"); continue
            rates_payload = {
                "HOURLY": {"base_value": float(request.form.get(f"{vt_code}_HOURLY_base_value",0)), "fraction_minutes": int(request.form.get(f"{vt_code}_HOURLY_fraction_minutes",30)), "first_hour_full": request.form.get(f"{vt_code}_HOURLY_first_hour_full")=="true"},
                "DAILY": {"base_value": float(request.form.get(f"{vt_code}_DAILY_base_value",0))},
                "NIGHTLY": {"base_value": float(request.form.get(f"{vt_code}_NIGHTLY_base_value",0)), "start_time": request.form.get(f"{vt_code}_NIGHTLY_start_time","21:00"), "end_time": request.form.get(f"{vt_code}_NIGHTLY_end_time","09:00")}
            }
            payload_to_send = {"rates": rates_payload, "abono_monthly_base_fee": float(request.form.get(f"{vt_code}_abono_monthly_base_fee",0))}
            response = requests.post(f"http://cashier-service:5001/api/rates/{vt_code}", json=payload_to_send, timeout=5)
            if response.status_code == 200: flash(f"Base rates for {vt_code} updated.", "success")
            else: flash(f"Failed for {vt_code}: {response.json().get('error',response.text)}", "error")
    except ValueError: flash("Invalid number format in rate fields.", "error")
    except requests.exceptions.RequestException: flash("Could not update rates: cashier service unavailable.", "error")
    except Exception as e: app.logger.error(f"Error updating base rates: {e}", exc_info=True); flash("Unexpected error.", "error")
    return redirect(url_for('admin_settings'))

# --- Payment Processing Routes ---
@app.route('/process_payment/<session_id>', methods=['GET', 'POST'])
@login_required
@roles_required(['admin', 'supervisor'])
def process_payment(session_id):
    if parking_sessions_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    try: session_obj_id = ObjectId(session_id)
    except Exception: flash('Invalid session ID format.', 'error'); return redirect(url_for('index'))
    session_data = parking_sessions_collection.find_one({'_id': session_obj_id})
    if not session_data: flash('Parking session not found.', 'error'); return redirect(url_for('index'))
    if not session_data.get('vehicle_type'): flash('Vehicle type not set. Please edit session first.', 'warning'); return redirect(url_for('edit_session', session_id=session_id))

    if request.method == 'POST':
        try:
            amount_received = float(request.form.get('amount_received'))
            calculated_total_charge = float(request.form.get('calculated_total_charge'))
            if amount_received < calculated_total_charge: flash('Amount received is less than total charge.', 'error'); return redirect(url_for('process_payment', session_id=session_id))
            change_given = amount_received - calculated_total_charge
            payment_payload = {
                "session_id": session_id, "calculated_total_charge": calculated_total_charge,
                "amount_received": amount_received, "change_given": round(change_given, 2),
                "payment_timestamp_iso": datetime.utcnow().isoformat(), "cashier_user_id": current_user.id,
                "modality_applied": request.form.get('modality_applied'),
                "inflation_factor_applied": float(request.form.get('inflation_factor_applied')),
                "duration_minutes": float(request.form.get('duration_minutes'))
            }
            response = requests.post('http://cashier-service:5001/api/record-payment', json=payment_payload, timeout=5)
            if response.status_code == 200: flash(f"Payment recorded. Change: ARS {change_given:.2f}.", "success")
            else: flash(f"Failed to record payment: {response.json().get('error', response.text)}", "error")
        except ValueError: flash("Invalid number for amount received.", "error"); return redirect(url_for('process_payment', session_id=session_id))
        except requests.exceptions.RequestException: flash("Could not record payment: cashier service unavailable.", "error")
        except Exception as e: app.logger.error(f"Error processing payment: {e}", exc_info=True); flash("Unexpected error.", "error")
        return redirect(url_for('index'))

    charge_info, error_calculating_charge = None, None
    try:
        payload = {"session_id": session_id, "manual_exit_timestamp": datetime.utcnow().isoformat()}
        response = requests.post('http://cashier-service:5001/api/calculate-charge', json=payload, timeout=5)
        if response.status_code == 200: charge_info = response.json()
        else: error_calculating_charge = response.json().get('error', f"Status {response.status_code}")
    except requests.exceptions.RequestException: error_calculating_charge = "Cashier service unavailable."
    except Exception as e: app.logger.error(f"Error calling calculate-charge: {e}", exc_info=True); error_calculating_charge = "Unexpected error."
    if error_calculating_charge: flash(f"Error from cashier service: {error_calculating_charge}", "error")
    
    return render_template('process_payment.html', session=session_data, charge_info=charge_info, error_calculating_charge=error_calculating_charge)

@app.route('/raw_detections')
@login_required
@roles_required(['admin', 'technical', 'supervisor'])
def raw_detections_list():
    detections = []
    error_message = None
    plate_query = request.args.get('plate_query', '').strip()
    
    if client is None or detections_collection is None:
        error_message = "MongoDB connection or detections collection not available."
        flash(error_message, "error")
    else:
        try:
            mongo_filter = {}
            if plate_query:
                # Case-insensitive partial match
                mongo_filter['plate_number'] = {"$regex": plate_query, "$options": "i"}
            
            detections_cursor = detections_collection.find(mongo_filter).sort("timestamp", -1).limit(50) # Still limit results
            for doc in detections_cursor:
                entry_path = doc.get('image_path')
                if entry_path and isinstance(entry_path, str):
                    doc['image_url'] = url_for('static', filename=entry_path[1:] if entry_path.startswith('/') else entry_path)
                else:
                    doc['image_url'] = None # Ensure image_url key exists
                detections.append(doc)
            
            if plate_query and not detections:
                flash(f"No detections found matching '{plate_query}'. Displaying latest if any.", "info")
                # Optionally, if query and no results, could clear filter and show latest
                # For now, it will just show an empty table if query yields no results.

        except Exception as e:
            app.logger.error(f"Error fetching raw detections for dedicated page: {e}", exc_info=True)
            error_message = "Error fetching raw detections."
            flash(error_message, "error")
            
    return render_template('raw_detections.html', 
                           detections=detections, 
                           error_message=error_message, 
                           current_user=current_user,
                           plate_query=plate_query) # Pass query back to template

if __name__ == '__main__':
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try: os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
        except OSError: pass 
    app.run(host='0.0.0.0', port=5000, debug=True)
