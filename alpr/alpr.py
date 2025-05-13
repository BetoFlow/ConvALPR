import os
import cv2
import numpy as np
import logging
from timeit import default_timer as timer
from datetime import datetime
import uuid # For unique filenames

from detector import PlateDetector # Changed from relative
from ocr import PlateOCR # Changed from relative
from saver import MongoSaver # Changed from relative

logger = logging.getLogger(__name__)

# Define the base path for saving detection images inside the container
DETECTION_IMAGE_DIR = "/app/detections"

class ALPR(MongoSaver): # Changed inheritance
    def __init__(self, 
                 mongo_uri: str,
                 detector_input_size: int = 512,
                 detector_confidence_threshold: float = 0.25,
                 ocr_model_number: int = 3,
                 ocr_avg_confidence_threshold: float = 0.60,
                 ocr_low_confidence_threshold: float = 0.35,
                 mongo_insert_frequency: int = 10,
                 save_detections_to_db: bool = True): # Added save_detections_to_db flag

        super().__init__(
            mongo_uri=mongo_uri,
            frequency_insert=mongo_insert_frequency
        )
        
        if detector_input_size not in (384, 512, 608):
            raise ValueError(
                f'Detector model for input size {detector_input_size} does not exist! Options {{384, 512, 608}}'
            )
        # Adjust model path for Docker environment
        detector_path = f'models/detection/tf-yolo_tiny_v4-{detector_input_size}x{detector_input_size}-custom-anchors/'
        self.detector = PlateDetector(
            detector_path, detector_input_size, score=detector_confidence_threshold
        )
        self.ocr = PlateOCR(
            ocr_model_number, ocr_avg_confidence_threshold, ocr_low_confidence_threshold
        )
        self.save_detections_to_db = save_detections_to_db
        
        # Ensure detection image directory exists
        if not os.path.exists(DETECTION_IMAGE_DIR):
            try:
                os.makedirs(DETECTION_IMAGE_DIR)
                logger.info(f"Created directory for detection images: {DETECTION_IMAGE_DIR}")
            except OSError as e:
                logger.error(f"Could not create directory {DETECTION_IMAGE_DIR}: {e}")
                # If directory can't be created, saving images will fail.
                # This might be an issue if permissions are wrong for the Docker volume.

    def process_frame(self, frame: np.ndarray, camera_id: str) -> list:
        """
        Processes a single frame to detect and recognize license plates.
        Saves detection information and images if configured.

        Parameters:
            frame (np.ndarray): The input frame (RGB).
            camera_id (str): Identifier for the camera/video source.
        
        Returns:
            list: A list of dictionaries, where each dictionary contains
                  details of a recognized plate (plate_number, confidence, bbox).
                  Returns empty list if no plates are recognized or on error.
        """
        processed_plates_info = []

        # Preprocess
        input_img = self.detector.preprocess(frame)
        # Inference
        yolo_out = self.detector.predict(input_img)
        # Bounding Boxes after NMS
        bboxes = self.detector.procesar_salida_yolo(yolo_out)
        
        # Perform OCR on each detected plate
        # self.ocr.predict expects iter_coords and frame.
        # iter_coords is yielded by self.detector.yield_coords(frame, bboxes)
        # self.ocr.predict returns a list of plate strings. This needs to be richer.
        # Let's assume self.ocr.predict_rich returns more info or we adapt it.
        # For now, let's iterate and call a more detailed OCR method per plate.

        for x1, y1, x2, y2, class_id in self.detector.yield_coords(frame, bboxes):
            # Perform OCR on the detected plate region
            # The original ocr.predict_ocr(x1, y1, x2, y2, frame) returns (plate_chars_list, char_probabilities_list)
            plate_chars, char_probs = self.ocr.predict_ocr(x1, y1, x2, y2, frame)
            
            if plate_chars: # plate_chars is a list of characters
                # If plate_chars is not empty, char_probs (numpy array of probabilities) should be valid for np.mean
                # The problematic 'if char_probs' (where char_probs is a numpy array) is removed.
                avg_confidence = np.mean(char_probs) if char_probs.size > 0 else 0.0
                # Apply OCR confidence thresholds
                if avg_confidence >= self.ocr.confianza_avg and self.ocr.none_low(char_probs, thresh=self.ocr.none_low_thresh):
                    plate_number = "".join(plate_chars).replace("_", "")
                    
                    logger.info(f"Plate detected: {plate_number} with confidence {avg_confidence:.2f} from {camera_id}")
                    
                    # Save image
                    timestamp_str = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
                    unique_id = uuid.uuid4().hex[:6]
                    image_filename = f"plate_{plate_number}_{camera_id}_{timestamp_str}_{unique_id}.jpg"
                    image_path_in_volume = os.path.join(DETECTION_IMAGE_DIR, image_filename)
                    
                    # The image_path for DB should be relative to the volume root as per PDR
                    # e.g. /detections/filename.jpg
                    db_image_path = os.path.join("/detections", image_filename)

                    try:
                        # Crop the plate from the original frame or save the whole frame
                        # For simplicity, saving the whole frame. Crop if needed:
                        # plate_image = frame[y1:y2, x1:x2]
                        # cv2.imwrite(image_path_in_volume, cv2.cvtColor(plate_image, cv2.COLOR_RGB2BGR))
                        cv2.imwrite(image_path_in_volume, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
                        logger.info(f"Saved detection image to {image_path_in_volume}")
                    except Exception as e:
                        logger.error(f"Failed to save image {image_path_in_volume}: {e}")
                        db_image_path = None # Do not store path if image saving failed

                    # Add to MongoDB batch if saving is enabled and image path is valid
                    if self.save_detections_to_db and self.collection is not None and db_image_path: # Corrected collection check
                        self.add_detection_record(
                            camera_id=camera_id,
                            plate_number=plate_number,
                            confidence=float(avg_confidence * 100), # PDR confidence is 0-100
                            image_path=db_image_path
                        )
                    
                    processed_plates_info.append({
                        "plate_number": plate_number,
                        "confidence": float(avg_confidence * 100),
                        "bbox": [x1, y1, x2, y2],
                        "image_path": db_image_path
                    })
        
        return processed_plates_info

    def mostrar_predicts(self, frame: np.ndarray):
        """
        Mostrar localizador + reconocedor

        Parametros:
            frame: np.ndarray sin procesar (Colores en orden: RGB)
        Returns:
            frame con el bounding box de la patente y
            la prediccion del texto de la patente

            total_time: tiempo de inferencia sin contar el dibujo
            de los rectangulos
        """
        total_time = 0
        start = timer()
        # Preprocess
        input_img = self.detector.preprocess(frame)
        # Inference
        yolo_out = self.detector.predict(input_img)
        # Bounding Boxes despues de NMS
        bboxes = self.detector.procesar_salida_yolo(yolo_out)
        # Hacer y mostrar OCR
        iter_coords = self.detector.yield_coords(frame, bboxes)
        end = timer()
        total_time += end - start
        fontScale = 1.25
        for yolo_prediction in iter_coords:
            x1, y1, x2, y2, _ = yolo_prediction
            #
            cv2.rectangle(frame, (x1, y1), (x2, y2), (36, 255, 12), 2)
            #
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
