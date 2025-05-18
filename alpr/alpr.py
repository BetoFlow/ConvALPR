import os
import cv2
import numpy as np
import logging
from timeit import default_timer as timer
from datetime import datetime, timedelta # Added timedelta
import uuid 

from detector import PlateDetector
from ocr import PlateOCR
from saver import MongoSaver

logger = logging.getLogger(__name__)

DETECTION_IMAGE_DIR = "/app/detections"
PARKING_SESSIONS_COLLECTION_NAME = "parking_sessions"
PLATE_LAST_SEEN_LOG_COLLECTION_NAME = "plate_last_seen_log" # New collection for debounce

class ALPR(MongoSaver):
    def __init__(self, 
                 mongo_uri: str, 
                 db_instance, 
                 log_raw_detections: bool = True, 
                 plate_cooldown_seconds: int = 300, # New parameter
                 detector_input_size: int = 512,
                 detector_confidence_threshold: float = 0.25,
                 ocr_model_number: int = 3,
                 ocr_avg_confidence_threshold: float = 0.60,
                 ocr_low_confidence_threshold: float = 0.35,
                 mongo_insert_frequency: int = 10):

        super().__init__( 
            mongo_uri=mongo_uri,
            frequency_insert=mongo_insert_frequency,
            log_raw_detections=log_raw_detections
        )
        
        self.db_instance = db_instance
        self.plate_cooldown_seconds = plate_cooldown_seconds # Store cooldown
        self.plate_last_seen_log_collection = None

        if self.db_instance is not None: 
            self.parking_sessions_collection = self.db_instance[PARKING_SESSIONS_COLLECTION_NAME]
            self.plate_last_seen_log_collection = self.db_instance[PLATE_LAST_SEEN_LOG_COLLECTION_NAME] # Init new collection
            logger.info(f"ALPR parking logic: Successfully initialized parking_sessions_collection and plate_last_seen_log_collection.")
        else:
            self.parking_sessions_collection = None
            logger.warning("ALPR parking logic: db_instance not provided or None. Parking session and cooldown logic will be disabled.")

        if detector_input_size not in (384, 512, 608):
            raise ValueError(
                f'Detector model for input size {detector_input_size} does not exist! Options {{384, 512, 608}}'
            )
        detector_path = f'models/detection/tf-yolo_tiny_v4-{detector_input_size}x{detector_input_size}-custom-anchors/'
        self.detector = PlateDetector(
            detector_path, detector_input_size, score=detector_confidence_threshold
        )
        self.ocr = PlateOCR(
            ocr_model_number, ocr_avg_confidence_threshold, ocr_low_confidence_threshold
        )
        
        if not os.path.exists(DETECTION_IMAGE_DIR):
            try:
                os.makedirs(DETECTION_IMAGE_DIR)
                logger.info(f"Created directory for detection images: {DETECTION_IMAGE_DIR}")
            except OSError as e:
                logger.error(f"Could not create directory {DETECTION_IMAGE_DIR}: {e}")

    def process_frame(self, frame: np.ndarray, camera_id: str, camera_role: str) -> list:
        processed_plates_info = []
        input_img = self.detector.preprocess(frame)
        yolo_out = self.detector.predict(input_img)
        bboxes = self.detector.procesar_salida_yolo(yolo_out)
        
        current_frame_timestamp = datetime.utcnow() # Timestamp for all detections in this frame

        for x1, y1, x2, y2, class_id in self.detector.yield_coords(frame, bboxes):
            plate_chars, char_probs = self.ocr.predict_ocr(x1, y1, x2, y2, frame)
            
            if plate_chars:
                avg_confidence = np.mean(char_probs) if char_probs.size > 0 else 0.0
                if avg_confidence >= self.ocr.confianza_avg and self.ocr.none_low(char_probs, thresh=self.ocr.none_low_thresh):
                    plate_number = "".join(plate_chars).replace("_", "")
                    
                    # Debounce Logic (per plate, per camera)
                    if self.plate_last_seen_log_collection is not None and self.plate_cooldown_seconds > 0:
                        cooldown_delta = timedelta(seconds=self.plate_cooldown_seconds)
                        last_seen_record = self.plate_last_seen_log_collection.find_one({
                            "plate_number": plate_number,
                            "camera_id": camera_id
                        })
                        if last_seen_record and (current_frame_timestamp - last_seen_record['timestamp'] < cooldown_delta):
                            logger.info(f"Plate {plate_number} at {camera_id} re-detected within cooldown period ({current_frame_timestamp - last_seen_record['timestamp'] < cooldown_delta}). Ignoring.")
                            continue # Skip further processing for this debounced plate

                    logger.info(f"Plate detected (passed cooldown): {plate_number} with confidence {avg_confidence:.2f} from {camera_id}")
                    
                    timestamp_str = current_frame_timestamp.strftime("%Y%m%d_%H%M%S_%f")
                    unique_id = uuid.uuid4().hex[:6]
                    image_filename = f"plate_{plate_number}_{camera_id}_{timestamp_str}_{unique_id}.jpg"
                    image_path_in_volume = os.path.join(DETECTION_IMAGE_DIR, image_filename)
                    db_image_path = os.path.join("/detections", image_filename)

                    try:
                        cv2.imwrite(image_path_in_volume, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        logger.info(f"Saved detection image to {image_path_in_volume}")
                    except Exception as e:
                        logger.error(f"Failed to save image {image_path_in_volume}: {e}")
                        db_image_path = None 

                    # Parking Logic
                    parking_logic_processed = False
                    if self.parking_sessions_collection is not None:
                        try:
                            active_session = self.parking_sessions_collection.find_one({
                                "plate_number": plate_number, "status": "inside"
                            })
                            if active_session: 
                                if camera_role == "entry":
                                    logger.warning(f"Plate {plate_number} at ENTRY camera {camera_id} but already 'inside'. Updating last_seen.")
                                    uresult = self.parking_sessions_collection.update_one({"_id": active_session["_id"]}, {"$set": {"last_seen_timestamp": current_frame_timestamp, "last_seen_camera_id": camera_id}})
                                    logger.info(f"Last_seen update for {plate_number} ack: {uresult.acknowledged}")
                                elif camera_role == "exit" or camera_role == "common":
                                    logger.info(f"Plate {plate_number} EXITING at {camera_role} camera {camera_id}.")
                                    uresult = self.parking_sessions_collection.update_one({"_id": active_session["_id"]}, {"$set": {"exit_timestamp": current_frame_timestamp, "exit_image_path": db_image_path, "exit_camera_id": camera_id, "status": "exited", "last_seen_timestamp": current_frame_timestamp, "last_seen_camera_id": camera_id}})
                                    logger.info(f"Exit update for {plate_number} ack: {uresult.acknowledged}")
                                elif camera_role == "monitoring":
                                    uresult = self.parking_sessions_collection.update_one({"_id": active_session["_id"]}, {"$set": {"last_seen_timestamp": current_frame_timestamp, "last_seen_camera_id": camera_id}})
                                    logger.info(f"Monitoring update for {plate_number} ack: {uresult.acknowledged}")
                                parking_logic_processed = True
                            else: 
                                if camera_role == "exit":
                                    logger.warning(f"Plate {plate_number} at EXIT camera {camera_id} but no active session.")
                                else: 
                                    logger.info(f"Plate {plate_number} ENTERING at {camera_role} camera {camera_id}.")
                                    new_session = {
                                        "plate_number": plate_number, 
                                        "entry_timestamp": current_frame_timestamp, 
                                        "entry_image_path": db_image_path, 
                                        "entry_camera_id": camera_id, 
                                        "exit_timestamp": None, 
                                        "exit_image_path": None, 
                                        "exit_camera_id": None, 
                                        "status": "inside", 
                                        "last_seen_timestamp": current_frame_timestamp, 
                                        "last_seen_camera_id": camera_id,
                                        "vehicle_type": None, # Added: Default to None, to be set via UI
                                        "price_per_hour": None, # Placeholder for future rate association
                                        "calculated_cost": None, # Placeholder
                                        "payment_status": "unpaid" # Default payment status
                                    }
                                    iresult = self.parking_sessions_collection.insert_one(new_session)
                                    logger.info(f"Parking session for {plate_number} created with vehicle_type=None, payment_status=unpaid. Ack: {iresult.acknowledged}")
                                    parking_logic_processed = True
                        except Exception as e_parking:
                            logger.error(f"Error during parking logic for {plate_number}: {e_parking}", exc_info=True)
                    else: 
                        logger.debug("Parking session collection N/A. Skipping parking logic.")

                    # Update plate_last_seen_log if parking logic or raw log was actioned
                    if self.plate_last_seen_log_collection is not None and (parking_logic_processed or (self.log_raw_detections and db_image_path)):
                        try:
                            lsl_result = self.plate_last_seen_log_collection.update_one(
                                {"plate_number": plate_number, "camera_id": camera_id},
                                {"$set": {"timestamp": current_frame_timestamp}},
                                upsert=True
                            )
                            logger.debug(f"Updated plate_last_seen_log for {plate_number} at {camera_id}. Ack: {lsl_result.acknowledged}")
                        except Exception as e_lsl:
                             logger.error(f"Error updating plate_last_seen_log for {plate_number}: {e_lsl}", exc_info=True)


                    if db_image_path: 
                        logger.info(f"Attempting to log raw detection for plate {plate_number} from {camera_id}. MongoSaver's log_raw_detections: {self.log_raw_detections}")
                        super().add_detection_record(
                            camera_id=camera_id, plate_number=plate_number,
                            confidence=float(avg_confidence * 100), image_path=db_image_path
                        )
                    
                    processed_plates_info.append({
                        "plate_number": plate_number, "confidence": float(avg_confidence * 100),
                        "bbox": [x1, y1, x2, y2], "image_path": db_image_path,
                        "camera_role": camera_role 
                    })
        
        return processed_plates_info

    def mostrar_predicts(self, frame: np.ndarray):
        total_time = 0
        start = timer()
        input_img = self.detector.preprocess(frame)
        yolo_out = self.detector.predict(input_img)
        bboxes = self.detector.procesar_salida_yolo(yolo_out)
        iter_coords = self.detector.yield_coords(frame, bboxes)
        end = timer()
        total_time += end - start
        fontScale = 1.25
        for yolo_prediction in iter_coords:
            x1, y1, x2, y2, _ = yolo_prediction
            cv2.rectangle(frame, (x1, y1), (x2, y2), (36, 255, 12), 2)
            start = timer()
            plate, probs = self.ocr.predict_ocr(x1, y1, x2, y2, frame)
            total_time += timer() - start
            avg = np.mean(probs)
            if avg > self.ocr.confianza_avg and self.ocr.none_low(probs, thresh=self.ocr.none_low_thresh):
                plate = (''.join(plate)).replace('_', '')
                mostrar_txt = f'{plate} {avg * 100:.2f}%'
                cv2.putText(img=frame, text=mostrar_txt, org=(x1 - 20, y1 - 15),
                            fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=fontScale,
                            color=[0, 0, 0], lineType=cv2.LINE_AA, thickness=6)
                cv2.putText(img=frame, text=mostrar_txt, org=(x1 - 20, y1 - 15),
                            fontFace=cv2.FONT_HERSHEY_SIMPLEX, fontScale=fontScale,
                            color=[255, 255, 255], lineType=cv2.LINE_AA, thickness=2)
        return frame, total_time
