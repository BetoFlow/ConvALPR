import os
import cv2
import threading
import time
import logging
import pymongo # For sort order
from datetime import datetime # For updating job timestamps
from alpr import ALPR # Assuming ALPR class is in alpr.py

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')
logger = logging.getLogger(__name__)

# Environment variable names
ENV_MONGO_URI = "MONGO_URI"
# ENV_VIDEO_SOURCE_PREFIX = "VIDEO_SOURCE_" # No longer used for stream configs
ENV_DB_NAME = "DB_NAME" # For specifying the database name
ENV_DETECTOR_INPUT_SIZE = "DETECTOR_INPUT_SIZE"
ENV_DETECTOR_CONFIDENCE = "DETECTOR_CONFIDENCE_THRESHOLD"
ENV_OCR_MODEL_NUM = "OCR_MODEL_NUMBER"
ENV_OCR_AVG_CONFIDENCE = "OCR_AVG_CONFIDENCE_THRESHOLD"
ENV_OCR_LOW_CONFIDENCE = "OCR_LOW_CONFIDENCE_THRESHOLD"
ENV_MONGO_INSERT_FREQ = "MONGO_INSERT_FREQUENCY"
ENV_INFERENCE_FREQUENCY_FRAMES = "INFERENCE_FREQUENCY_FRAMES" # How many frames to skip
ENV_LOG_RAW_DETECTIONS = "LOG_RAW_DETECTIONS" 
ENV_PLATE_COOLDOWN_SECONDS = "PLATE_DETECTION_COOLDOWN_SECONDS" # New: For detection debounce

DEFAULT_MONGO_URI = "mongodb://mongodb:27017/anpr_db" # Default if not set, matches docker-compose
DEFAULT_DETECTOR_INPUT_SIZE = 512
DEFAULT_DETECTOR_CONFIDENCE = 0.25
DEFAULT_OCR_MODEL_NUM = 3
DEFAULT_OCR_AVG_CONFIDENCE = 0.60
DEFAULT_OCR_LOW_CONFIDENCE = 0.35
DEFAULT_MONGO_INSERT_FREQ = 10
DEFAULT_INFERENCE_FREQUENCY_FRAMES = 30 # Process 1 frame every 30
DEFAULT_LOG_RAW_DETECTIONS = True 
DEFAULT_PLATE_COOLDOWN_SECONDS = 300 # New: Default 5 minutes (300 seconds)

# Global ALPR instance (shared by threads, if its methods are thread-safe)
# MongoSaver part of ALPR should handle concurrent calls to add_detection_record if it uses thread-safe list appends
# or if each thread gets its own ALPR instance (safer but more memory).
# For now, let's assume ALPR's process_frame is okay to be called, and MongoSaver's batching is okay.
# A single ALPR instance is better for TF models to avoid loading them multiple times.
alpr_instance = None
mongo_client = None # To share client for video job watcher
db_instance = None # To share db for video job watcher and stream configs

VIDEO_JOBS_COLLECTION_NAME = 'video_jobs'
STREAM_CONFIGS_COLLECTION_NAME = 'stream_configs' # For persistent stream configs
JOB_POLL_INTERVAL_SECONDS = 10 # How often to check for new video jobs

def get_env_var(name, default, var_type=str):
    value = os.environ.get(name, default)
    try:
        if var_type == bool: # Special handling for bool
            # Ensure 'value' is a string before calling .lower()
            return str(value).lower() in ('true', '1', 't')
        return var_type(value)
    except ValueError:
        logger.warning(f"Invalid value for environment variable {name}: '{value}'. Using default: {default}.")
        return default

def process_video_stream(video_source_uri: str, camera_id: str, camera_role: str, alpr: ALPR, inference_freq_frames: int): # Added camera_role
    logger.info(f"Thread started for {camera_id} (Role: {camera_role}) with source: {video_source_uri}")
    cap = cv2.VideoCapture(video_source_uri)
    if not cap.isOpened():
        logger.error(f"Failed to open video stream: {video_source_uri} for {camera_id}")
        return

    frame_count = 0
    debug_frame_save_count = 0
    max_debug_frames_to_save = 5 # Save a few sample frames for debugging
    debug_frame_interval = inference_freq_frames * 10 # Save a debug frame less frequently than processing

    # Ensure debug directory exists (it's on the shared volume via /app/detections)
    debug_frame_dir = "/app/detections/debug_frames"
    if not os.path.exists(debug_frame_dir):
        try:
            os.makedirs(debug_frame_dir)
        except OSError as e:
            logger.warning(f"Could not create debug_frames directory {debug_frame_dir}: {e}")
            # Continue without saving debug frames if dir creation fails

    while True:
        ret, frame = cap.read()
        if not ret:
            logger.warning(f"Stream {camera_id}: Error reading frame or stream ended (frame_count: {frame_count}). Attempting to reconnect...")
            cap.release()
            reconnect_delay = 5 # Initial delay in seconds
            while True: # Indefinite reconnection loop
                logger.info(f"Stream {camera_id}: Waiting {reconnect_delay}s before attempting to reconnect...")
                time.sleep(reconnect_delay)
                cap = cv2.VideoCapture(video_source_uri)
                if cap.isOpened():
                    logger.info(f"Stream {camera_id}: Reconnected successfully.")
                    frame_count = 0 # Reset frame count for new capture
                    debug_frame_save_count = 0 # Reset debug save count
                    break # Break from inner reconnection loop, continue outer frame processing loop
                else:
                    logger.warning(f"Stream {camera_id}: Reconnect failed. Will retry.")
                    reconnect_delay = min(reconnect_delay * 2, 300) # Exponential backoff up to 5 minutes (300s)
            continue # Continue to the next iteration of the main frame reading loop (outer while True)
        
        # Log frame shape for debugging, but not too frequently to avoid spamming logs
        if frame_count % (inference_freq_frames * 5) == 0: # Log shape every 5*inference_freq_frames
             logger.info(f"{camera_id}: Read frame {frame_count}, shape: {frame.shape if frame is not None else 'None'}")

        # Save a sample frame for debugging periodically
        if frame is not None and os.path.exists(debug_frame_dir) and \
           debug_frame_save_count < max_debug_frames_to_save and \
           frame_count > 0 and frame_count % debug_frame_interval == 0:
            try:
                debug_filename = f"{debug_frame_dir}/{camera_id}_frame_{frame_count}_shape_{frame.shape[1]}x{frame.shape[0]}.jpg"
                cv2.imwrite(debug_filename, frame)
                logger.info(f"{camera_id}: Saved debug frame to {debug_filename}")
                debug_frame_save_count += 1
            except Exception as e:
                logger.warning(f"{camera_id}: Failed to save debug frame: {e}")


        if frame_count % inference_freq_frames == 0:
            if frame is None: # Should not happen if ret is True, but as a safeguard
                logger.warning(f"{camera_id}: Frame object is None at frame_count {frame_count}, skipping processing.")
                frame_count +=1
                continue
            try:
                # Assuming frame is BGR from OpenCV, ALPR expects RGB
                frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                detections = alpr.process_frame(frame_rgb, camera_id, camera_role) # Pass camera_role
                if detections: # process_frame now returns list of processed_plate_info or raw detections based on its internal logic
                    # This log might need adjustment depending on what process_frame returns with parking logic
                    logger.info(f"{camera_id} (Role: {camera_role}) processed frame {frame_count}. Result: {len(detections)} items.")
                # else:
                #     logger.debug(f"{camera_id} processed frame {frame_count}, no plates found.")

            except Exception as e:
                logger.error(f"Error processing frame from {camera_id}: {e}", exc_info=True)
        
        frame_count += 1
        # Optional: add a small delay if CPU usage is too high, or rely on inference_freq_frames
        # time.sleep(0.01) 

    cap.release()
    logger.info(f"Thread finished for {camera_id}")


def process_single_video_file(video_path: str, job_id: str, camera_id: str, alpr: ALPR, inference_freq_frames: int, video_jobs_coll):
    """
    Processes a single uploaded video file.
    """
    # For uploaded files, assume a "common" role for now, or make it configurable later if needed.
    # The parking logic in ALPR.process_frame will handle this role.
    assumed_role_for_uploaded_video = "common" 
    logger.info(f"Processing job {job_id}: video file {video_path} for camera {camera_id} (Assumed Role: {assumed_role_for_uploaded_video})")
    
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error(f"Job {job_id}: Failed to open video file: {video_path}")
        video_jobs_coll.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "processed_at": datetime.utcnow()},
             "$push": {"anpr_service_messages": f"Failed to open video file: {video_path}"}}
        )
        return

    frame_count = 0
    processed_something = False
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.info(f"Job {job_id}: End of video file {video_path}.")
                break

            if frame_count % inference_freq_frames == 0:
                try:
                    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    detections = alpr.process_frame(frame_rgb, camera_id, assumed_role_for_uploaded_video) # Pass assumed_role
                    if detections:
                        processed_something = True
                        logger.info(f"Job {job_id} ({camera_id}, Role: {assumed_role_for_uploaded_video}): processed frame {frame_count}. Result: {len(detections)} items.")
                except Exception as e:
                    logger.error(f"Job {job_id} ({camera_id}): Error processing frame: {e}", exc_info=True)
                    video_jobs_coll.update_one(
                        {"job_id": job_id},
                        {"$push": {"anpr_service_messages": f"Error processing frame {frame_count}: {str(e)}"}}
                    )
            frame_count += 1
        
        # Update job status to completed
        status_message = "Processed successfully."
        if not processed_something:
            status_message = "Processed successfully, no plates found meeting criteria."
        
        video_jobs_coll.update_one(
            {"job_id": job_id},
            {"$set": {"status": "completed", "processed_at": datetime.utcnow()},
             "$push": {"anpr_service_messages": status_message}}
        )
        logger.info(f"Job {job_id} ({camera_id}): Processing completed for {video_path}.")

    except Exception as e:
        logger.error(f"Job {job_id} ({camera_id}): Unhandled error during video processing of {video_path}: {e}", exc_info=True)
        video_jobs_coll.update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "processed_at": datetime.utcnow()},
             "$push": {"anpr_service_messages": f"Unhandled error: {str(e)}"}}
        )
    finally:
        cap.release()
        # Optionally, delete the video file after processing
        # try:
        #     os.remove(video_path)
        #     logger.info(f"Job {job_id}: Deleted processed video file {video_path}")
        # except OSError as e:
        #     logger.error(f"Job {job_id}: Error deleting video file {video_path}: {e}")


def watch_video_jobs(alpr: ALPR, db: pymongo.database.Database, inference_freq_frames: int):
    logger.info("Video job watcher thread started.")
    video_jobs_coll = db[VIDEO_JOBS_COLLECTION_NAME]
    
    while True:
        try:
            # Find one pending job, oldest first
            job = video_jobs_coll.find_one_and_update(
                {"status": "pending"},
                {"$set": {"status": "processing", "processed_at": datetime.utcnow()}},
                sort=[("uploaded_at", pymongo.ASCENDING)] # Process oldest first
            )

            if job:
                logger.info(f"Picked up video processing job: {job['job_id']} for file {job['video_path']}")
                # Construct a camera_id for this job, e.g., from job_id or original filename
                camera_id_for_job = f"upload_{job.get('original_filename', job['job_id'])}"
                # Sanitize camera_id if needed
                camera_id_for_job = "".join(c if c.isalnum() or c in ['_','-'] else '_' for c in camera_id_for_job)[:50]


                # Run processing in a new thread to not block the watcher
                processing_thread = threading.Thread(
                    target=process_single_video_file,
                    args=(job['video_path'], job['job_id'], camera_id_for_job, alpr, inference_freq_frames, video_jobs_coll),
                    name=f"JobProcessor-{job['job_id'][:8]}"
                )
                processing_thread.start()
            else:
                # No pending jobs, wait a bit
                time.sleep(JOB_POLL_INTERVAL_SECONDS)
        
        except pymongo.errors.PyMongoError as e:
            logger.error(f"MongoDB error in video job watcher: {e}", exc_info=True)
            time.sleep(JOB_POLL_INTERVAL_SECONDS * 2) # Longer sleep on DB error
        except Exception as e:
            logger.error(f"Unexpected error in video job watcher: {e}", exc_info=True)
            time.sleep(JOB_POLL_INTERVAL_SECONDS)


def main():
    global alpr_instance, mongo_client, db_instance
    
    mongo_uri = get_env_var(ENV_MONGO_URI, DEFAULT_MONGO_URI)
    detector_input_size = get_env_var(ENV_DETECTOR_INPUT_SIZE, DEFAULT_DETECTOR_INPUT_SIZE, int)
    detector_confidence = get_env_var(ENV_DETECTOR_CONFIDENCE, DEFAULT_DETECTOR_CONFIDENCE, float)
    ocr_model_num = get_env_var(ENV_OCR_MODEL_NUM, DEFAULT_OCR_MODEL_NUM, int)
    ocr_avg_confidence = get_env_var(ENV_OCR_AVG_CONFIDENCE, DEFAULT_OCR_AVG_CONFIDENCE, float)
    ocr_low_confidence = get_env_var(ENV_OCR_LOW_CONFIDENCE, DEFAULT_OCR_LOW_CONFIDENCE, float)
    mongo_insert_freq = get_env_var(ENV_MONGO_INSERT_FREQ, DEFAULT_MONGO_INSERT_FREQ, int)
    inference_freq = get_env_var(ENV_INFERENCE_FREQUENCY_FRAMES, DEFAULT_INFERENCE_FREQUENCY_FRAMES, int)
    log_raw_detections_flag = get_env_var(ENV_LOG_RAW_DETECTIONS, DEFAULT_LOG_RAW_DETECTIONS, bool)
    plate_cooldown_seconds = get_env_var(ENV_PLATE_COOLDOWN_SECONDS, DEFAULT_PLATE_COOLDOWN_SECONDS, int)

    # Initialize MongoDB client and ALPR instance
    # ALPR class itself handles its Mongo connection for MongoSaver (for raw detections if enabled)
    # But we need a client/db object for the job watcher and stream configs.
    db_name_to_use = get_env_var(ENV_DB_NAME, "anpr_db")
    try:
        mongo_client = pymongo.MongoClient(mongo_uri)
        mongo_client.admin.command('ismaster') # Verify connection
        db_instance = mongo_client[db_name_to_use]
        logger.info(f"Successfully connected to MongoDB ({db_name_to_use}) for service operations: {mongo_uri}")
    except pymongo.errors.ConnectionFailure:
        logger.error(f"MongoDB connection failed at {mongo_uri}. Service might not function correctly.")
        # Exit if core DB connection fails? For now, it might proceed and ALPR init will also try.
    except Exception as e:
        logger.error(f"An error occurred during MongoDB initialization: {e}")


    logger.info("Initializing ANPR system...")
    # Pass db_instance to ALPR for parking logic access to other collections
    # Pass log_raw_detections_flag to control raw logging via MongoSaver
    alpr_instance = ALPR(
        mongo_uri=mongo_uri, # For MongoSaver's own connection (if still used directly or for raw logs)
        db_instance=db_instance, 
        log_raw_detections=log_raw_detections_flag,
        plate_cooldown_seconds=plate_cooldown_seconds, # Pass new param
        detector_input_size=detector_input_size,
        detector_confidence_threshold=detector_confidence,
        ocr_model_number=ocr_model_num,
        ocr_avg_confidence_threshold=ocr_avg_confidence,
        ocr_low_confidence_threshold=ocr_low_confidence,
        mongo_insert_frequency=mongo_insert_freq
    )

    if alpr_instance.collection is None and log_raw_detections_flag: # Check MongoSaver's collection only if raw logging is on
        logger.error("Failed to connect to MongoDB. ANPR service will run without database saving.")
        # Decide if service should exit or run without DB. PDR implies DB is crucial.
        # For now, it continues but MongoSaver methods will log warnings.

    threads = []
    if db_instance is not None: # Corrected check: Proceed only if DB connection for service ops was successful
        # Fetch and start persistent video streams from MongoDB
        stream_configs_coll = db_instance[STREAM_CONFIGS_COLLECTION_NAME]
        try:
            configured_streams = list(stream_configs_coll.find())
            if configured_streams:
                logger.info(f"Found {len(configured_streams)} persistent stream(s) in config.")
                for stream_config in configured_streams:
                    cam_id = stream_config.get("name", f"stream_{stream_config['_id']}")
                    src_uri = stream_config.get("url")
                    if src_uri:
                        # Convert numerical source to int if it's all digits (for USB cams)
                        processed_src_uri = int(src_uri) if src_uri.isdigit() else src_uri
                        camera_role = stream_config.get("role", "common") # Get role, default to "common"
                        thread = threading.Thread(
                            target=process_video_stream,
                            args=(processed_src_uri, cam_id, camera_role, alpr_instance, inference_freq), # Pass camera_role
                            name=f"StreamProcessor-{cam_id}"
                        )
                        threads.append(thread)
                        thread.start()
                    else:
                        logger.warning(f"Stream config '{cam_id}' missing 'url'. Skipping.")
            else:
                logger.info("No persistent video streams configured in the database.")
        except pymongo.errors.PyMongoError as e:
            logger.error(f"Error fetching stream configurations from MongoDB: {e}")
        
        # Start thread for watching video jobs from uploads
        job_watcher_thread = threading.Thread(
            target=watch_video_jobs,
            args=(alpr_instance, db_instance, inference_freq),
            name="VideoJobWatcher"
        )
        threads.append(job_watcher_thread)
        job_watcher_thread.daemon = True # Allow main program to exit even if this thread is running
        job_watcher_thread.start()
    else:
        logger.error("Cannot start stream processors or job watcher due to MongoDB connection failure for service operations.")
    # Main loop to keep service running and handle shutdown gracefully.
    non_daemon_stream_threads = [t for t in threads if not t.daemon and t.name.startswith("StreamProcessor-")]
    job_watcher_thread_instance = next((t for t in threads if t.name == "VideoJobWatcher"), None)

    try:
        if non_daemon_stream_threads:
            logger.info(f"Main thread waiting for {len(non_daemon_stream_threads)} non-daemon stream processor thread(s) to complete.")
            for thread in non_daemon_stream_threads:
                thread.join() # Wait for all non-daemon stream processors
            logger.info("All non-daemon stream processor threads have completed.")

        # If the job watcher is running (as a daemon), keep the main thread alive.
        # This loop will also be interrupted by KeyboardInterrupt.
        if job_watcher_thread_instance and job_watcher_thread_instance.is_alive():
            logger.info("Stream processors finished (or none were non-daemon). Keeping main thread alive for VideoJobWatcher.")
            while job_watcher_thread_instance.is_alive():
                time.sleep(1.0) # Keep main thread alive by checking the daemon thread
            logger.info("VideoJobWatcher thread has exited.")
        elif not non_daemon_stream_threads:
            # No persistent streams and job watcher didn't start or exited.
            logger.info("No active non-daemon threads and job watcher is not running. Service may exit if no work.")
            # If we reach here, it means no persistent streams were configured or they finished,
            # and the job watcher isn't running (e.g. DB issue).
            # The service_main.py might exit if there's nothing to keep it alive.
            # This is acceptable if there's truly no work. Docker will restart if configured.

    except KeyboardInterrupt:
        logger.info("Shutdown signal (KeyboardInterrupt) received. Initiating graceful shutdown.")
    except Exception as e:
        logger.error(f"Unexpected error in main thread: {e}", exc_info=True)
    finally:
        if alpr_instance: # ALPR instance handles its own Mongo client for saving detections
            alpr_instance.flush_remaining_records()
        if mongo_client: # Close the client used by the job watcher
            mongo_client.close()
            logger.info("MongoDB client for job watcher closed.")
        logger.info("ANPR service stopped.")

if __name__ == "__main__":
    main()
