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

app = Flask(__name__)

# Configuration
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'super_secret_key_for_dev_only_change_me')
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = 'anpr_db'
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


# Flask-Login setup
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'
bcrypt = Bcrypt(app)

# MongoDB Client Initialization
client = None
db = None
detections_collection, video_jobs_collection, stream_configs_collection, parking_sessions_collection, users_collection = None, None, None, None, None

def create_initial_admin_user():
    app.logger.info("Attempting to create initial admin user...")
    if users_collection is None:
        app.logger.warning("users_collection is None, cannot create initial admin user.")
        return
    try:
        doc_count = users_collection.count_documents({})
        app.logger.info(f"Found {doc_count} documents in users collection.")
        if doc_count == 0:
            app.logger.info("Users collection is empty, proceeding to create admin user.")
            hashed_password = bcrypt.generate_password_hash("admin").decode('utf-8')
            user_doc = {"username": "admin", "password_hash": hashed_password, "roles": ["admin", "operator"]}
            insert_result = users_collection.insert_one(user_doc)
            if insert_result.inserted_id:
                app.logger.info(f"Created initial admin user with ID {insert_result.inserted_id} (username 'admin', password 'admin'). Please change this password.")
            else:
                app.logger.error("Failed to insert initial admin user, but no exception raised.")
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

def admin_required(f): return roles_required(['admin'])(f)

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
    detections, stream_configs, parking_sessions, error_message = [], [], [], None
    if client is None: error_message = "MongoDB connection failed. Data cannot be loaded."
    else:
        try:
            if detections_collection is not None:
                detections_cursor = detections_collection.find().sort("timestamp", -1).limit(50)
                for doc in detections_cursor:
                    entry_path = doc.get('image_path')
                    if entry_path and isinstance(entry_path, str):
                        doc['image_url'] = url_for('static', filename=entry_path[1:] if entry_path.startswith('/') else entry_path)
                    else:
                        doc['image_url'] = None
                    detections.append(doc)
        except Exception as e: app.logger.error(f"Error fetching raw detections: {e}"); error_message = (error_message + " " if error_message else "") + "Error fetching raw detections."
        
        try:
            if parking_sessions_collection is not None:
                sessions_cursor = parking_sessions_collection.find().sort("entry_timestamp", -1).limit(50)
                for s in sessions_cursor:
                    entry_path = s.get('entry_image_path')
                    if entry_path and isinstance(entry_path, str):
                        s['entry_image_url'] = url_for('static', filename=entry_path[1:] if entry_path.startswith('/') else entry_path)
                    else:
                        s['entry_image_url'] = None
                    
                    exit_path = s.get('exit_image_path')
                    if exit_path and isinstance(exit_path, str):
                        s['exit_image_url'] = url_for('static', filename=exit_path[1:] if exit_path.startswith('/') else exit_path)
                    else:
                        s['exit_image_url'] = None

                    if s.get('status') == 'exited' and s.get('entry_timestamp') and s.get('exit_timestamp'): s['duration_str'] = str(s['exit_timestamp'] - s['entry_timestamp']).split('.')[0]
                    else: s['duration_str'] = "N/A"
                    parking_sessions.append(s)
        except Exception as e: app.logger.error(f"Error fetching parking sessions: {e}", exc_info=True); error_message = (error_message + " " if error_message else "") + "Error fetching parking sessions."
        
        try:
            if stream_configs_collection is not None:
                stream_configs = list(stream_configs_collection.find().sort("name", 1))
        except Exception as e: app.logger.error(f"Error fetching stream configs: {e}"); error_message = (error_message + " " if error_message else "") + "Error fetching stream configs."
            
    return render_template('index.html', detections=detections, stream_configs=stream_configs, parking_sessions=parking_sessions, error_message=error_message, current_user=current_user)

@app.route('/upload', methods=['POST'])
@login_required
@roles_required(['admin', 'technical', 'supervisor']) # Removed 'operator'
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
            app.logger.info(f"Video saved to {save_path}")
            anpr_service_video_path = os.path.join(PATH_FOR_ANPR_SERVICE_READ_PREFIX, filename)
            job_id = uuid.uuid4().hex
            job_record = {"job_id": job_id, "video_path": anpr_service_video_path, "original_filename": original_filename, "status": "pending", "uploaded_at": datetime.utcnow(), "processed_at": None, "anpr_service_messages": []}
            video_jobs_collection.insert_one(job_record)
            flash(f"Video '{original_filename}' uploaded (Job ID: {job_id}).", 'success')
        except Exception as e:
            app.logger.error(f"Error uploading video: {e}", exc_info=True); flash(f"Error: {e}", 'error')
            if 'save_path' in locals() and os.path.exists(save_path):
                try: os.remove(save_path)
                except OSError as rm_e: app.logger.error(f"Error cleaning file {save_path}: {rm_e}")
        return redirect(url_for('index'))
    else: flash('Invalid file type. Allowed: ' + ", ".join(ALLOWED_EXTENSIONS), 'error'); return redirect(url_for('index'))

@app.route('/add_stream', methods=['POST'])
@login_required
@roles_required(['admin', 'technical'])
def add_stream():
    if stream_configs_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    stream_name = request.form.get('stream_name'); stream_url = request.form.get('stream_url'); stream_camera_role = request.form.get('stream_role')
    allowed_camera_roles = ["common", "entry", "exit", "monitoring"]
    if not (stream_name and stream_url and stream_camera_role): flash('Stream name, URL, and camera role are required.', 'error'); return redirect(url_for('index'))
    if stream_camera_role not in allowed_camera_roles: flash(f"Invalid camera role '{stream_camera_role}'.", 'error'); return redirect(url_for('index'))
    try:
        if stream_configs_collection.find_one({"name": stream_name}): flash(f"Stream name '{stream_name}' already exists.", 'error'); return redirect(url_for('index'))
        stream_record = {"name": stream_name, "url": stream_url, "role": stream_camera_role, "added_at": datetime.utcnow()}
        stream_configs_collection.insert_one(stream_record)
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

@app.route('/edit_session/<session_id>', methods=['GET', 'POST'])
@login_required
@roles_required(['admin', 'supervisor'])
def edit_session(session_id):
    if parking_sessions_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    try: session_obj_id = ObjectId(session_id)
    except Exception: flash('Invalid session ID.', 'error'); return redirect(url_for('index'))
    session_data = parking_sessions_collection.find_one({'_id': session_obj_id})
    if not session_data: flash('Parking session not found.', 'error'); return redirect(url_for('index'))
    
    entry_path_edit = session_data.get('entry_image_path')
    if entry_path_edit and isinstance(entry_path_edit, str):
        session_data['entry_image_url'] = url_for('static', filename=entry_path_edit[1:] if entry_path_edit.startswith('/') else entry_path_edit)
    else:
        session_data['entry_image_url'] = None

    exit_path_edit = session_data.get('exit_image_path')
    if exit_path_edit and isinstance(exit_path_edit, str):
        session_data['exit_image_url'] = url_for('static', filename=exit_path_edit[1:] if exit_path_edit.startswith('/') else exit_path_edit)
    else:
        session_data['exit_image_url'] = None
        
    if request.method == 'POST':
        new_status = request.form.get('status'); new_exit_timestamp_str = request.form.get('exit_timestamp')
        update_fields = {}
        if new_status in ['inside', 'exited']: update_fields['status'] = new_status
        else: flash('Invalid status.', 'error'); return render_template('edit_session.html', session=session_data)
        if new_exit_timestamp_str:
            try: update_fields['exit_timestamp'] = datetime.strptime(new_exit_timestamp_str, '%Y-%m-%dT%H:%M')
            except ValueError: flash('Invalid exit timestamp format.', 'error'); return render_template('edit_session.html', session=session_data)
        else: update_fields['exit_timestamp'] = None; update_fields['exit_image_path'] = None; update_fields['exit_camera_id'] = None
        if new_status == 'inside': update_fields['exit_timestamp'] = None; update_fields['exit_image_path'] = None; update_fields['exit_camera_id'] = None
        if new_status == 'exited' and not update_fields.get('exit_timestamp'): flash('Exit timestamp required for "exited" status.', 'error'); return render_template('edit_session.html', session=session_data)
        if update_fields:
            try:
                parking_sessions_collection.update_one({'_id': session_obj_id}, {'$set': update_fields})
                flash('Parking session updated.', 'success'); return redirect(url_for('index'))
            except Exception as e: app.logger.error(f"Error updating session {session_id}: {e}", exc_info=True); flash('Error updating session.', 'error')
        else: flash('No changes submitted.', 'info')
        
        session_data_updated_post = parking_sessions_collection.find_one({'_id': session_obj_id}) 
        if session_data_updated_post: 
            session_data = session_data_updated_post 
            entry_path_post_update = session_data.get('entry_image_path')
            if entry_path_post_update and isinstance(entry_path_post_update, str):
                session_data['entry_image_url'] = url_for('static', filename=entry_path_post_update[1:] if entry_path_post_update.startswith('/') else entry_path_post_update)
            else:
                session_data['entry_image_url'] = None
            exit_path_post_update = session_data.get('exit_image_path')
            if exit_path_post_update and isinstance(exit_path_post_update, str):
                session_data['exit_image_url'] = url_for('static', filename=exit_path_post_update[1:] if exit_path_post_update.startswith('/') else exit_path_post_update)
            else:
                session_data['exit_image_url'] = None
    return render_template('edit_session.html', session=session_data)

@app.route('/users', methods=['GET'])
@login_required
@roles_required(['admin', 'supervisor']) 
def manage_users():
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('index'))
    all_users_cursor = users_collection.find()
    all_users = []
    admin_users_count = 0
    for user_data in all_users_cursor:
        user_data['_id'] = str(user_data['_id'])
        if 'admin' in user_data.get('roles', []): admin_users_count += 1
        all_users.append(user_data)
    return render_template('manage_users.html', users=all_users, admin_users_count=admin_users_count)

@app.route('/users/add', methods=['POST'])
@login_required
@roles_required(['admin']) 
def add_user():
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('manage_users'))
    username = request.form.get('username'); password = request.form.get('password'); roles = request.form.getlist('roles')
    if not (username and password): flash('Username and password required.', 'error'); return redirect(url_for('manage_users'))
    for role in roles:
        if role not in VALID_APP_USER_ROLES: flash(f"Invalid role '{role}' selected.", 'error'); return redirect(url_for('manage_users'))
    if not roles: roles = ['operator']; flash('No roles selected, defaulting to "operator".', 'info')
    if users_collection.find_one({"username": username}): flash(f"Username '{username}' already exists.", 'error'); return redirect(url_for('manage_users'))
    try:
        hashed_password = bcrypt.generate_password_hash(password).decode('utf-8')
        users_collection.insert_one({"username": username, "password_hash": hashed_password, "roles": roles})
        flash(f"User '{username}' created with roles: {', '.join(roles)}.", 'success')
    except Exception as e: app.logger.error(f"Error creating user '{username}': {e}", exc_info=True); flash('Error creating user.', 'error')
    return redirect(url_for('manage_users'))

@app.route('/users/edit/<user_id>', methods=['GET', 'POST'])
@login_required
@roles_required(['admin', 'supervisor'])
def edit_user(user_id):
    if users_collection is None: flash('Database service not available.', 'error'); return redirect(url_for('manage_users'))
    try: user_obj_id = ObjectId(user_id)
    except Exception: flash('Invalid user ID.', 'error'); return redirect(url_for('manage_users'))
    user_data = users_collection.find_one({'_id': user_obj_id})
    if not user_data: flash('User not found.', 'error'); return redirect(url_for('manage_users'))
    
    user_for_template = dict(user_data)
    user_for_template['roles'] = user_data.get('roles', [])

    if request.method == 'POST':
        new_password = request.form.get('password'); confirm_password = request.form.get('confirm_password')
        new_roles = request.form.getlist('roles')
        update_fields = {}

        if current_user.has_role('supervisor') and not current_user.has_role('admin'):
            if user_data['username'] == 'admin': flash("Supervisors cannot edit the primary 'admin' user.", 'error'); return redirect(url_for('manage_users'))
            if 'admin' in new_roles: flash("Supervisors cannot grant 'admin' role.", 'error'); new_roles = [r for r in new_roles if r != 'admin']
        
        if new_password:
            if new_password != confirm_password: flash('Passwords do not match.', 'error'); return render_template('edit_user.html', user=user_for_template)
            if len(new_password) < 4: flash('Password must be at least 4 characters.', 'error'); return render_template('edit_user.html', user=user_for_template)
            update_fields['password_hash'] = bcrypt.generate_password_hash(new_password).decode('utf-8')

        for role in new_roles:
            if role not in VALID_APP_USER_ROLES: flash(f"Invalid role '{role}'.", 'error'); return render_template('edit_user.html', user=user_for_template)
        
        is_editing_self = current_user.id == user_id
        if 'admin' not in new_roles and ('admin' in user_data.get('roles',[])): 
             if is_editing_self or user_data['username'] == 'admin': 
                other_admins_count = users_collection.count_documents({"roles": "admin", "_id": {"$ne": user_obj_id}})
                if other_admins_count == 0:
                    flash("Cannot remove 'admin' role, would leave no administrators.", 'error')
                    return render_template('edit_user.html', user=user_for_template)
        
        update_fields['roles'] = new_roles if new_roles else ['operator']

        if update_fields:
            try:
                users_collection.update_one({'_id': user_obj_id}, {'$set': update_fields})
                flash(f"User '{user_data['username']}' updated.", 'success'); return redirect(url_for('manage_users'))
            except Exception as e: app.logger.error(f"Error updating user {user_id}: {e}", exc_info=True); flash('Error updating user.', 'error')
        else: flash('No changes submitted.', 'info')
        
        user_data_updated = users_collection.find_one({'_id': user_obj_id}); user_for_template = dict(user_data_updated); user_for_template['roles'] = user_data_updated.get('roles', [])
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
        if user_to_delete['username'] == 'admin':
            other_admins_count = users_collection.count_documents({"roles": "admin", "username": {"$ne": "admin"}})
            if other_admins_count == 0: flash("Cannot delete primary 'admin' if it's the only admin.", 'error'); return redirect(url_for('manage_users'))
        users_collection.delete_one({"_id": ObjectId(user_id)})
        flash(f"User '{user_to_delete['username']}' deleted.", 'success')
    except Exception as e: app.logger.error(f"Error deleting user {user_id}: {e}", exc_info=True); flash('Error deleting user.', 'error')
    return redirect(url_for('manage_users'))

if __name__ == '__main__':
    if not os.path.exists(PATH_FOR_WEBPORTAL_SAVE):
        try: os.makedirs(PATH_FOR_WEBPORTAL_SAVE)
        except OSError: pass 
    app.run(host='0.0.0.0', port=5000, debug=True)
