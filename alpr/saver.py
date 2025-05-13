"""
Logic related to saving detections to MongoDB.
"""
import os
import logging
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure, OperationFailure
from datetime import datetime

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

class MongoSaver:
    """
    Handles saving detection information to a MongoDB database.
    """

    def __init__(self, mongo_uri: str, db_name: str = 'anpr_db', 
                 collection_name: str = 'detections', frequency_insert: int = 10,
                 log_raw_detections: bool = True): # Added log_raw_detections
        """
        mongo_uri: The MongoDB connection string.
        db_name: Name of the database.
        collection_name: Name of the collection.
        frequency_insert: How many records to accumulate before inserting into the database.
        """
        self.mongo_uri = mongo_uri
        self.db_name = db_name
        self.collection_name = collection_name
        self.frequency_insert = frequency_insert
        self.log_raw_detections = log_raw_detections # Store the flag
        self.records_batch = []
        self.client = None
        self.db = None
        self.collection = None # This is for the raw detections collection

        if not self.log_raw_detections:
            logger.info("Raw detection logging is disabled. MongoSaver will not save to 'detections' collection.")
            return # Don't initialize client/collection if not logging raw

        try:
            self.client = MongoClient(self.mongo_uri)
            # The ismaster command is cheap and does not require auth.
            self.client.admin.command('ismaster') 
            self.db = self.client[self.db_name]
            self.collection = self.db[self.collection_name]
            logger.info(f"Successfully connected to MongoDB: {self.mongo_uri}")
        except ConnectionFailure:
            logger.error(f"MongoDB connection failed at {self.mongo_uri}. Saver will not work.")
            # Allow application to continue, but saver won't function
        except Exception as e:
            logger.error(f"An error occurred during MongoDB initialization: {e}")

    def add_detection_record(self, camera_id: str, plate_number: str, confidence: float, image_path: str):
        """
        Adds a single detection record to the current batch.
        Triggers a batch insert if the batch size meets frequency_insert.

        Parameters:
            camera_id (str): Identifier for the camera/video source.
            plate_number (str): The identified license plate.
            confidence (float): Confidence score of the ANPR detection.
            image_path (str): Path to the saved detection image.
        """
        logger.debug(f"MongoSaver.add_detection_record called. log_raw_detections: {self.log_raw_detections}, collection is None: {self.collection is None}")
        if not self.log_raw_detections:
            logger.debug("Raw detection logging is off, skipping add_detection_record.")
            return 

        if self.collection is None: 
            logger.warning("MongoDB collection for raw detections not available. Cannot add detection record.")
            return

        record = {
            "camera_id": camera_id,
            "plate_number": plate_number,
            "confidence": confidence,
            "timestamp": datetime.utcnow(), # Use UTC time for consistency
            "image_path": image_path
        }
        self.records_batch.append(record)

        if len(self.records_batch) >= self.frequency_insert:
            self.insert_batch_to_mongo()

    def insert_batch_to_mongo(self):
        """
        Inserts the current batch of records into MongoDB.
        Clears the batch after insertion.
        """
        if self.collection is None: # Corrected check
            logger.warning("MongoDB collection not available. Cannot insert batch.")
            return

        if not self.records_batch:
            return

        try:
            self.collection.insert_many(self.records_batch)
            logger.info(f"Successfully inserted {len(self.records_batch)} records into MongoDB.")
            self.records_batch.clear()
        except OperationFailure as e:
            logger.error(f"MongoDB batch insert failed: {e}")
            # Optionally, handle retry logic or save failed batches
        except Exception as e:
            logger.error(f"An unexpected error occurred during MongoDB batch insert: {e}")


    def flush_remaining_records(self):
        """
        Inserts any remaining records in the batch to MongoDB.
        Useful to call before application shutdown.
        """
        if self.records_batch:
            logger.info(f"Flushing {len(self.records_batch)} remaining records to MongoDB.")
            self.insert_batch_to_mongo()

    def __del__(self):
        """
        Ensures any remaining records are flushed and closes the MongoDB client connection.
        """
        if self.records_batch: # Ensure any pending records are saved
            self.flush_remaining_records()
        if self.client:
            self.client.close()
            logger.info("MongoDB client connection closed.")
