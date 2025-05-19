import logging
from datetime import datetime
from transitions import Machine
from bson import ObjectId

logger = logging.getLogger(__name__)

class ParkingSessionStateMachine:
    """
    Manages the state of a parking session using a state machine.
    Interacts with a MongoDB collection to persist state changes.
    """

    STATES = [
        'INIT',  # Initial state before any event
        'VEHICLE_ENTERED',
        'AWAITING_PAYMENT_RESOLUTION',
        'PAID_AWAITING_EXIT',
        'SESSION_UNPAID_TIMEOUT', # Intermediate state before closing as unpaid
        'SESSION_CLOSED'
    ]

    def __init__(self, session_id: ObjectId, initial_state: str, mongo_collection, session_data: dict = None):
        """
        Initializes the ParkingSessionStateMachine.

        :param session_id: The MongoDB ObjectId of the session.
        :param initial_state: The current state of the session from DB.
        :param mongo_collection: The PyMongo collection object for parking_sessions.
        :param session_data: The full session document, if available.
        """
        if not session_id:
            raise ValueError("session_id is required")
        if mongo_collection is None: # Corrected check for PyMongo Collection
            raise ValueError("mongo_collection is required")
        if initial_state not in self.STATES:
            logger.warning(f"Initial state '{initial_state}' not in defined STATES. Defaulting to INIT.")
            initial_state = 'INIT'
            # Or, attempt to fetch from DB if session_data is not provided
            # and update initial_state based on that. For now, require it.

        self.session_id = session_id
        self.mongo_collection = mongo_collection
        self.session_data = session_data if session_data else {} # Store for context

        self.machine = Machine(
            model=self,
            states=ParkingSessionStateMachine.STATES,
            initial=initial_state,
            send_event=True, # Allows passing event_data to callbacks
            after_state_change='_persist_state' # Persist after any state change
        )

        # Define transitions
        # Format: machine.add_transition(trigger, source, dest, conditions=None, unless=None, before=None, after=None)

        # Entry detection
        self.machine.add_transition(
            trigger='event_detect_entry',
            source='INIT', # Assuming new sessions start here for the FSM
            dest='VEHICLE_ENTERED',
            before='_prepare_entry_data'
        )

        # Exit detection
        self.machine.add_transition(
            trigger='event_detect_exit',
            source='VEHICLE_ENTERED',
            dest='AWAITING_PAYMENT_RESOLUTION',
            before='_prepare_exit_data'
        )
        self.machine.add_transition(
            trigger='event_detect_exit',
            source='PAID_AWAITING_EXIT',
            dest='SESSION_CLOSED',
            before='_prepare_exit_data' # Still record exit details
        )
        # What if exit detected on AWAITING_PAYMENT_RESOLUTION? (e.g. re-detection) - For now, no change.
        # Or if exit detected on VEHICLE_PARKED (if that state is used more formally)

        # Payment received
        self.machine.add_transition(
            trigger='payment_received', # Renamed trigger
            source='VEHICLE_ENTERED', # Payment before exit detection
            dest='PAID_AWAITING_EXIT',
            before='_prepare_payment_data'
        )
        self.machine.add_transition(
            trigger='payment_received', # Renamed trigger
            source='AWAITING_PAYMENT_RESOLUTION', # Payment after exit detection
            dest='SESSION_CLOSED',
            before='_prepare_payment_data'
        )
        self.machine.add_transition(
            trigger='payment_received', # Renamed trigger
            source='SESSION_UNPAID_TIMEOUT', # Payment after timeout (if allowed by policy)
            dest='SESSION_CLOSED', # Or a specific "PAID_AFTER_TIMEOUT" if needed
            before='_prepare_payment_data'
        )

        # Payment timeout (grace period expired after exit detection)
        self.machine.add_transition(
            trigger='event_payment_timeout',
            source='AWAITING_PAYMENT_RESOLUTION',
            dest='SESSION_UNPAID_TIMEOUT', # First mark as timeout
            before='_prepare_timeout_data'
        )
        # Optional: Auto-close after timeout
        self.machine.add_transition(
            trigger='event_force_close_unpaid', # Could be triggered by a subsequent process
            source='SESSION_UNPAID_TIMEOUT',
            dest='SESSION_CLOSED',
            before='_prepare_close_unpaid_data'
        )
        
        # Manual or other closure events can be added if needed

        logger.debug(f"State machine initialized for session {self.session_id} in state {self.state}")

    def _update_session_data(self, event_data, update_fields):
        """Helper to merge event_data fields into session_data if they exist"""
        for key, value in update_fields.items():
            if hasattr(event_data, key):
                self.session_data[key] = getattr(event_data, key)
            elif key in event_data.kwargs: # if passed as kwargs to trigger
                 self.session_data[key] = event_data.kwargs[key]


    # --- Callback methods for state transitions ---

    def _prepare_entry_data(self, event_data):
        """Called before transitioning to VEHICLE_ENTERED."""
        logger.info(f"Session {self.session_id}: Preparing entry data. Event: {event_data.event.name}")
        # Fields to be set by the caller (ALPR service) via event_data.kwargs
        # e.g., entry_timestamp, entry_image_path, entry_camera_id, plate_number, vehicle_type
        self.session_data.update(event_data.kwargs)
        self.session_data['payment_status'] = 'unpaid'
        self.session_data['status'] = 'inside' # Old status field for compatibility

    def _prepare_exit_data(self, event_data):
        """Called before AWAITING_PAYMENT_RESOLUTION or SESSION_CLOSED (if from PAID_AWAITING_EXIT)."""
        logger.info(f"Session {self.session_id}: Preparing exit data. Event: {event_data.event.name}")
        # Fields: exit_timestamp, exit_image_path, exit_camera_id
        self.session_data.update(event_data.kwargs)
        if self.state == 'PAID_AWAITING_EXIT': # Destination will be SESSION_CLOSED
            self.session_data['status'] = 'exited'
            # payment_status should already be 'completed_paid' or similar
        else: # Destination will be AWAITING_PAYMENT_RESOLUTION
            self.session_data['status'] = 'inside' # Still considered inside until resolved or fully exited

    def _prepare_payment_data(self, event_data):
        """Called before PAID_AWAITING_EXIT or SESSION_CLOSED (if paid)."""
        logger.info(f"Session {self.session_id}: Preparing payment data. Event: {event_data.event.name}")
        # Fields: payment_details (dict from cashier), calculated_cost, payment_timestamp etc.
        payment_info = event_data.kwargs.get('payment_info', {})
        self.session_data.update(payment_info) # Merge all payment details
        self.session_data['payment_status'] = 'completed_paid' # Generic paid status
        
        if self.state == 'VEHICLE_ENTERED': # Destination PAID_AWAITING_EXIT
             self.session_data['status'] = 'inside'
             # payment_status in cashier service was 'paid_pending_exit', here we simplify
        elif self.state == 'AWAITING_PAYMENT_RESOLUTION' or self.state == 'SESSION_UNPAID_TIMEOUT': # Destination SESSION_CLOSED
             self.session_data['status'] = 'exited'


    def _prepare_timeout_data(self, event_data):
        """Called before SESSION_UNPAID_TIMEOUT."""
        logger.info(f"Session {self.session_id}: Preparing timeout data. Event: {event_data.event.name}")
        self.session_data['payment_status'] = 'unpaid_timeout'
        self.session_data['status'] = 'exited' # Considered exited for billing, but unpaid

    def _prepare_close_unpaid_data(self, event_data):
        """Called before SESSION_CLOSED from SESSION_UNPAID_TIMEOUT."""
        logger.info(f"Session {self.session_id}: Preparing to close as unpaid. Event: {event_data.event.name}")
        # Ensure final status reflects unpaid closure
        self.session_data['payment_status'] = self.session_data.get('payment_status', 'unpaid_timeout') # Keep existing unpaid status
        self.session_data['status'] = 'exited'


    def _persist_state(self, event_data):
        """
        Persists the current state and any modified session_data to MongoDB.
        This is an after_state_change callback.
        """
        new_state = self.state # Current state of the model
        logger.info(f"Session {self.session_id}: Persisting state change. Old: {event_data.transition.source}, New: {new_state}. Event: {event_data.event.name}")

        update_doc = {
            '$set': {
                'session_state': new_state,
                'last_state_update_timestamp': datetime.utcnow()
            }
        }
        # Add other fields from self.session_data that were modified by 'before' callbacks
        # We need to be careful not to overwrite fields unintentionally.
        # The _prepare_* methods should have updated self.session_data.
        
        # Only update fields that are part of the session_data managed by FSM
        # This is a simplified approach; a more robust way would be to track dirty fields.
        fields_to_set = {
            'payment_status': self.session_data.get('payment_status'),
            'status': self.session_data.get('status'), # Old status field
            'entry_timestamp': self.session_data.get('entry_timestamp'),
            'entry_image_path': self.session_data.get('entry_image_path'),
            'entry_camera_id': self.session_data.get('entry_camera_id'),
            'exit_timestamp': self.session_data.get('exit_timestamp'),
            'exit_image_path': self.session_data.get('exit_image_path'),
            'exit_camera_id': self.session_data.get('exit_camera_id'),
            'plate_number': self.session_data.get('plate_number'),
            'vehicle_type': self.session_data.get('vehicle_type'),
            # Payment details from _prepare_payment_data
            'calculated_cost': self.session_data.get('calculated_cost'),
            'amount_received': self.session_data.get('amount_received'),
            'change_given': self.session_data.get('change_given'),
            'payment_timestamp': self.session_data.get('payment_timestamp'),
            'payment_processed_by_user_id': self.session_data.get('payment_processed_by_user_id'),
            'payment_modality': self.session_data.get('payment_modality'),
            'payment_inflation_factor': self.session_data.get('payment_inflation_factor'),
            'payment_duration_minutes': self.session_data.get('payment_duration_minutes')
        }
        
        # Filter out None values to avoid unsetting fields unintentionally unless explicitly set to None
        filtered_fields_to_set = {k: v for k, v in fields_to_set.items() if v is not None or k in event_data.kwargs}
        # If a field was explicitly passed in kwargs (even as None), it should be set.
        # Example: if exit_timestamp=None is passed to clear it.

        if filtered_fields_to_set:
            update_doc['$set'].update(filtered_fields_to_set)
        
        # For new sessions being created via 'event_detect_entry'
        if event_data.event.name == 'event_detect_entry' and event_data.transition.source == 'INIT':
            # This is an insert operation, not an update
            # The session_id might be a placeholder if it's a truly new session.
            # The caller (ALPR) should handle the actual insert with all initial fields.
            # This _persist_state is more for updates.
            # Let's assume for now the document ALREADY EXISTS or is created by the caller,
            # and this FSM instance is for managing its state.
            # If session_id was a new ObjectId() for a session not yet in DB:
            # self.mongo_collection.insert_one({'_id': self.session_id, **update_doc['$set']})
            # For now, we assume updates.
            pass # Initial creation logic will be handled by ALPR service directly.
                 # This FSM is for updating an existing or newly created session.

        try:
            # Use upsert=True if the session might not exist (e.g. for event_detect_entry)
            # However, session_id should be fixed.
            # If it's a new session, the ALPR service should create the initial doc.
            # This FSM will then update it.
            
            # If the trigger was 'event_detect_entry', it's a new session.
            # The ALPR service will create the document with initial state.
            # This callback is more for subsequent state changes.
            # For simplicity, we'll make the calling code responsible for initial doc creation.
            # This method will only update.

            if event_data.transition.source != 'INIT': # Don't try to update for the initial pseudo-transition
                result = self.mongo_collection.update_one(
                    {'_id': self.session_id},
                    update_doc
                )
                if result.matched_count == 0:
                    logger.error(f"Session {self.session_id}: Failed to find document to persist state {new_state}.")
                elif result.modified_count == 0 and result.matched_count == 1:
                    logger.warning(f"Session {self.session_id}: Document matched but no fields were modified for state {new_state}. Update doc: {update_doc}")
                else:
                    logger.info(f"Session {self.session_id}: Successfully persisted state {new_state}. Matched: {result.matched_count}, Modified: {result.modified_count}")
            else:
                 logger.info(f"Session {self.session_id}: Initial state '{new_state}' set. Caller responsible for initial DB record creation if new.")

        except Exception as e:
            logger.error(f"Session {self.session_id}: Error persisting state {new_state} to MongoDB: {e}", exc_info=True)

    def trigger_event(self, event_name, **kwargs):
        """
        Triggers an event on the state machine.
        kwargs are passed to the transition's callback methods via event_data.
        """
        try:
            if hasattr(self, event_name): # Check if trigger method exists
                trigger_func = getattr(self, event_name)
                trigger_func(**kwargs) # Call the generated trigger method
                # Ensure True is returned on successful dispatch,
                # as transitions library event methods usually return None.
                logger.debug(f"Session {self.session_id}: Successfully dispatched event '{event_name}'. Current state: {self.state}")
                return True
            else:
                logger.error(f"Session {self.session_id}: Event '{event_name}' not found in state machine.")
                return False
        except Exception as e:
            logger.error(f"Session {self.session_id}: Error triggering event '{event_name}': {e}", exc_info=True)
            return False

    # Convenience method to load a session and its FSM
    @classmethod
    def load_session(cls, session_id_str: str, mongo_collection):
        try:
            session_oid = ObjectId(session_id_str)
        except Exception:
            logger.error(f"Invalid session_id format: {session_id_str}")
            return None
        
        session_doc = mongo_collection.find_one({"_id": session_oid})
        if not session_doc:
            logger.error(f"Session not found in DB: {session_id_str}")
            return None
        
        initial_state = session_doc.get("session_state", "INIT") # Default to INIT if not set
        return cls(session_id=session_oid, initial_state=initial_state, mongo_collection=mongo_collection, session_data=session_doc)
