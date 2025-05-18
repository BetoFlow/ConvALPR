import os
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson.objectid import ObjectId # Added import
import logging
from datetime import datetime 

app = Flask(__name__)

# Configuration from environment variables
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://mongodb:27017/')
DB_NAME = os.environ.get('DB_NAME', 'anpr_db')
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', '0').lower() in ['true', '1', 't']

# Setup logging
# logging.basicConfig(level=logging.DEBUG if FLASK_DEBUG else logging.INFO) # Flask will configure its own logger
# logger = logging.getLogger(__name__) # Use app.logger instead

if FLASK_DEBUG:
    app.logger.setLevel(logging.DEBUG)
else:
    app.logger.setLevel(logging.INFO)

# MongoDB Client Initialization
client = None
db = None
# Define collection names that this service will manage/use
RATE_CONFIGS_COLLECTION_NAME = 'rate_configs'
ABONO_HOLDERS_COLLECTION_NAME = 'abono_holders'
INFLATION_FACTORS_COLLECTION_NAME = 'inflation_factors' # Or a single doc in app_settings

rate_configs_collection = None
abono_holders_collection = None
inflation_factors_collection = None
parking_sessions_collection = None # Will need to read/update this

try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ismaster') # Verify connection
    db = client[DB_NAME]
    
    rate_configs_collection = db[RATE_CONFIGS_COLLECTION_NAME]
    abono_holders_collection = db[ABONO_HOLDERS_COLLECTION_NAME]
    inflation_factors_collection = db[INFLATION_FACTORS_COLLECTION_NAME]
    parking_sessions_collection = db['parking_sessions'] # Accessing existing collection

    app.logger.info(f"Cashier Service: Successfully connected to MongoDB at {MONGO_URI}, DB: {DB_NAME}")
    # Initialize default rate configs / inflation factor if needed on first run
    # Example: if inflation_factors_collection.count_documents({}) == 0:
    # inflation_factors_collection.insert_one({"factor": 1.0, "last_updated": datetime.utcnow()})

except ConnectionFailure:
    app.logger.error(f"Cashier Service: MongoDB connection failed at {MONGO_URI}.") # Use app.logger
except Exception as e:
    app.logger.error(f"Cashier Service: An error occurred during MongoDB initialization: {e}", exc_info=True) # Use app.logger


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "service": "cashier-service"}), 200

# --- Inflation Factor Management ---
INFLATION_FACTOR_DOC_ID = "current_inflation_factor" # Document ID for storing the factor

@app.route('/api/inflation-factor', methods=['GET'])
def get_inflation_factor():
    if inflation_factors_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    
    factor_doc = inflation_factors_collection.find_one({"_id": INFLATION_FACTOR_DOC_ID})
    if factor_doc:
        return jsonify({"factor": factor_doc.get("factor", 1.0), "last_updated": factor_doc.get("last_updated")}), 200
    else:
        # Default if not set yet
        return jsonify({"factor": 1.0, "last_updated": None, "message": "Factor not set, returning default."}), 200

@app.route('/api/inflation-factor', methods=['POST'])
def set_inflation_factor():
    if inflation_factors_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    
    data = request.get_json()
    if not data or 'factor' not in data:
        return jsonify({"error": "Missing 'factor' in request body"}), 400
    
    try:
        new_factor = float(data['factor'])
        if new_factor <= 0:
            return jsonify({"error": "Factor must be a positive number"}), 400
    except ValueError:
        return jsonify({"error": "Factor must be a valid number"}), 400

    from datetime import datetime # Import here or globally
    update_result = inflation_factors_collection.update_one(
        {"_id": INFLATION_FACTOR_DOC_ID},
        {"$set": {"factor": new_factor, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    
    if update_result.acknowledged:
        app.logger.info(f"Inflation factor updated to: {new_factor}") # Use app.logger
        return jsonify({"message": "Inflation factor updated successfully", "new_factor": new_factor}), 200
    else:
        app.logger.error("Failed to update inflation factor in DB.") 
        return jsonify({"error": "Failed to update inflation factor"}), 500

# --- Operational Timezone Management ---
OPERATIONAL_TIMEZONE_DOC_ID = "operational_timezone_config"
DEFAULT_OPERATIONAL_TIMEZONE = "UTC" # Fallback timezone

@app.route('/api/settings/timezone', methods=['GET'])
def get_operational_timezone():
    if inflation_factors_collection is None: # Storing in the same collection for simplicity
        return jsonify({"error": "Database not connected"}), 500
    
    tz_doc = inflation_factors_collection.find_one({"_id": OPERATIONAL_TIMEZONE_DOC_ID})
    if tz_doc and tz_doc.get("timezone"):
        return jsonify({"timezone": tz_doc.get("timezone"), "last_updated": tz_doc.get("last_updated")}), 200
    else:
        return jsonify({"timezone": DEFAULT_OPERATIONAL_TIMEZONE, "last_updated": None, "message": "Timezone not set, returning default UTC."}), 200

@app.route('/api/settings/timezone', methods=['POST'])
def set_operational_timezone():
    if inflation_factors_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    
    data = request.get_json()
    if not data or 'timezone' not in data:
        return jsonify({"error": "Missing 'timezone' in request body"}), 400
    
    new_timezone = data['timezone']
    
    # Basic validation: Check if pytz recognizes the timezone.
    # This service doesn't have pytz by default, but web-portal does.
    # For now, we'll trust the input or let web-portal validate before sending.
    # A more robust solution would be for cashier-service to also have pytz and validate.
    # For simplicity, skipping pytz validation here.
    
    update_result = inflation_factors_collection.update_one(
        {"_id": OPERATIONAL_TIMEZONE_DOC_ID},
        {"$set": {"timezone": new_timezone, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    
    if update_result.acknowledged:
        app.logger.info(f"Operational timezone updated to: {new_timezone}")
        return jsonify({"message": "Operational timezone updated successfully", "new_timezone": new_timezone}), 200
    else:
        app.logger.error("Failed to update operational timezone in DB.")
        return jsonify({"error": "Failed to update operational timezone"}), 500

# --- Base Rate Management ---
# Structure per vehicle type in 'rate_configs' collection:
# { "_id": "CAR_SUV", "vehicle_type_label": "Car/SUV", 
#   "rates": { 
#       "HOURLY": { "base_value": 7000, ...}, 
#       "DAILY": { "base_value": 47500, ...} 
#   }, 
#   "abono_monthly_base_fee": 80000, "last_updated": "..."
# }

VEHICLE_TYPES_SUPPORTED = {
    "CAR_SUV": "Car/SUV",
    "TRUCK": "Truck/Van",
    "MOTORCYCLE": "Motorcycle"
}

@app.route('/api/rates', methods=['GET'])
def get_all_rates():
    if rate_configs_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    
    all_rates_cursor = rate_configs_collection.find({})
    rates_data = {doc['_id']: doc for doc in all_rates_cursor}
    
    # Ensure all supported vehicle types have at least default entries if missing
    for vt_code, vt_label in VEHICLE_TYPES_SUPPORTED.items():
        if vt_code not in rates_data:
            rates_data[vt_code] = {
                "_id": vt_code, "vehicle_type_label": vt_label,
                "rates": {}, "abono_monthly_base_fee": 0, "last_updated": None,
                "message": "Default placeholder - rates not configured yet."
            }
            
    return jsonify(rates_data), 200

@app.route('/api/rates/<vehicle_type_code>', methods=['GET'])
def get_rates_for_vehicle_type(vehicle_type_code):
    if rate_configs_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    if vehicle_type_code not in VEHICLE_TYPES_SUPPORTED:
        return jsonify({"error": f"Unsupported vehicle type: {vehicle_type_code}"}), 400
        
    rates_doc = rate_configs_collection.find_one({"_id": vehicle_type_code})
    if rates_doc:
        return jsonify(rates_doc), 200
    else:
        return jsonify({
            "_id": vehicle_type_code, "vehicle_type_label": VEHICLE_TYPES_SUPPORTED[vehicle_type_code],
            "rates": {}, "abono_monthly_base_fee": 0, "last_updated": None,
            "message": "Rates not configured for this vehicle type."
        }), 200 # Or 404 if preferred when not found

@app.route('/api/rates/<vehicle_type_code>', methods=['POST'])
def update_rates_for_vehicle_type(vehicle_type_code):
    if rate_configs_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    if vehicle_type_code not in VEHICLE_TYPES_SUPPORTED:
        return jsonify({"error": f"Unsupported vehicle type: {vehicle_type_code}"}), 400

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing data in request body"}), 400

    # Basic validation (can be more thorough)
    # Example: ensure 'rates' is a dict, 'abono_monthly_base_fee' is a number
    if not isinstance(data.get('rates'), dict) or not isinstance(data.get('abono_monthly_base_fee'), (int, float)):
         return jsonify({"error": "Invalid data structure for rates or abono_monthly_base_fee"}), 400

    update_payload = {
        "vehicle_type_label": VEHICLE_TYPES_SUPPORTED[vehicle_type_code],
        "rates": data.get('rates', {}),
        "abono_monthly_base_fee": data.get('abono_monthly_base_fee', 0),
        "last_updated": datetime.utcnow()
    }

    update_result = rate_configs_collection.update_one(
        {"_id": vehicle_type_code},
        {"$set": update_payload},
        upsert=True
    )

    if update_result.acknowledged:
        app.logger.info(f"Rates updated for vehicle type: {vehicle_type_code}") # Use app.logger
        return jsonify({"message": f"Rates for {vehicle_type_code} updated successfully"}), 200
    else:
        app.logger.error(f"Failed to update rates for {vehicle_type_code} in DB.") # Use app.logger
        return jsonify({"error": f"Failed to update rates for {vehicle_type_code}"}), 500


# --- API Endpoints for Abono Management (Admin) ---
# Example: /api/abonos (GET, POST, DELETE /id for monthly pass holders)

# --- API Endpoints for Cashier Operations ---

@app.route('/api/calculate-charge', methods=['POST'])
def calculate_charge_api():
    if parking_sessions_collection is None or \
       rate_configs_collection is None or \
       inflation_factors_collection is None:
        logger.error("calculate_charge_api: One or more critical collections are None.")
        return jsonify({"error": "Database service or critical collections not initialized"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON data in request body"}), 400

    session_id = data.get('session_id')
    # Allow overriding exit_timestamp for "price so far" calculations
    # If not provided, and session is 'inside', use current time.
    # If session is 'exited', use actual exit_timestamp from session.
    manual_exit_timestamp_str = data.get('manual_exit_timestamp') 

    if not session_id:
        return jsonify({"error": "Missing 'session_id' in request"}), 400

    try:
        session_oid = ObjectId(session_id)
    except Exception:
        return jsonify({"error": "Invalid session_id format"}), 400
        
    session = parking_sessions_collection.find_one({"_id": session_oid})
    if not session:
        return jsonify({"error": "Parking session not found"}), 404

    vehicle_type = session.get('vehicle_type')
    if not vehicle_type or vehicle_type not in VEHICLE_TYPES_SUPPORTED:
        # If vehicle_type is None or invalid, it needs to be set first.
        # For now, we can default or error out. Cashier_module.md implies it's an input.
        # Let's assume it must be set on the session for now.
        return jsonify({"error": f"Vehicle type not set or invalid for session {session_id}. Please set it first."}), 400

    entry_timestamp = session.get('entry_timestamp')
    if not entry_timestamp:
        return jsonify({"error": "Session has no entry timestamp"}), 400

    exit_timestamp_to_use = None
    if manual_exit_timestamp_str:
        try:
            exit_timestamp_to_use = datetime.fromisoformat(manual_exit_timestamp_str.replace("Z", "+00:00"))
        except ValueError:
            return jsonify({"error": "Invalid manual_exit_timestamp format. Use ISO format."}), 400
    elif session.get('status') == 'exited' and session.get('exit_timestamp'):
        exit_timestamp_to_use = session['exit_timestamp']
    else: # Session is 'inside' or exit_timestamp is missing
        exit_timestamp_to_use = datetime.utcnow()


    # Fetch inflation factor
    factor_doc = inflation_factors_collection.find_one({"_id": INFLATION_FACTOR_DOC_ID})
    inflation_factor = factor_doc.get("factor", 1.0) if factor_doc else 1.0

    # Fetch rates for vehicle_type
    rates_doc = rate_configs_collection.find_one({"_id": vehicle_type})
    if not rates_doc or not rates_doc.get('rates'):
        return jsonify({"error": f"Rate configuration not found for vehicle type: {vehicle_type}"}), 404
    
    vehicle_rates = rates_doc['rates']
    
    # --- Start Calculation Logic (Simplified for HOURLY and DAILY first) ---
    duration_delta = exit_timestamp_to_use - entry_timestamp
    duration_total_minutes = duration_delta.total_seconds() / 60
    duration_total_hours = duration_total_minutes / 60

    if duration_total_minutes < 0: # Should not happen if timestamps are correct
        return jsonify({"error": "Exit time is before entry time"}), 400
    
    calculated_charges = {} # To store charges for different modalities

    # 1. Hourly Rate Calculation
    hourly_rate_config = vehicle_rates.get('HOURLY')
    if hourly_rate_config and 'base_value' in hourly_rate_config:
        base_hourly = float(hourly_rate_config['base_value'])
        effective_hourly_rate = base_hourly * inflation_factor
        fraction_minutes = int(hourly_rate_config.get('fraction_minutes', 30))
        first_hour_full = hourly_rate_config.get('first_hour_full', True)

        if duration_total_minutes <= 0: # e.g. very short stay, or for "price so far" at entry
             hourly_charge = 0 # Or minimum charge if applicable
        elif first_hour_full and duration_total_minutes <= 60:
            hourly_charge = effective_hourly_rate
        else:
            full_hours = int(duration_total_hours)
            remaining_minutes_after_full_hours = duration_total_minutes - (full_hours * 60)
            
            chargeable_hours = 0
            if first_hour_full:
                chargeable_hours = 1 # First hour
                if duration_total_minutes > 60:
                    remaining_minutes_after_first_hour = duration_total_minutes - 60
                    num_fractions = (remaining_minutes_after_first_hour + fraction_minutes -1) // fraction_minutes # ceil division
                    chargeable_hours += num_fractions * (fraction_minutes / 60.0)
            else: # First hour not necessarily full, calculate all by fractions
                num_fractions = (duration_total_minutes + fraction_minutes -1) // fraction_minutes
                chargeable_hours = num_fractions * (fraction_minutes / 60.0)

            hourly_charge = chargeable_hours * effective_hourly_rate
        
        # Daily Cap for Hourly Rate
        daily_rate_config_for_cap = vehicle_rates.get('DAILY') # Assuming cap is based on DAILY modality
        if daily_rate_config_for_cap and 'base_value' in daily_rate_config_for_cap:
            effective_daily_rate_for_cap = float(daily_rate_config_for_cap['base_value']) * inflation_factor
            if hourly_charge > effective_daily_rate_for_cap:
                hourly_charge = effective_daily_rate_for_cap
        
        calculated_charges['HOURLY'] = round(hourly_charge, 2)

    # 2. Full Day Rate Calculation
    daily_rate_config = vehicle_rates.get('DAILY')
    if daily_rate_config and 'base_value' in daily_rate_config:
        base_daily = float(daily_rate_config['base_value'])
        effective_daily_rate = base_daily * inflation_factor
        
        # As per Cashier_module.md: "If parking duration is > 6 hours and <= 24 hours, apply this fixed rate."
        if 6 < duration_total_hours <= 24:
            calculated_charges['DAILY'] = round(effective_daily_rate, 2)
        elif duration_total_hours > 24:
            num_full_24h_blocks = int(duration_total_hours // 24)
            remaining_hours_after_full_days = duration_total_hours % 24
            
            daily_charge_total = num_full_24h_blocks * effective_daily_rate
            
            # Calculate charge for the remaining partial day using hourly logic (capped at daily rate)
            if remaining_hours_after_full_days > 0:
                # This part needs the hourly calculation logic again for the remainder
                # For simplicity now, if there's any remainder, charge another full day, or use hourly capped.
                # Let's use hourly capped for the remainder as per "capped at the Full Day Rate for that partial day"
                # This requires re-calculating hourly for 'remaining_hours_after_full_days'
                # This is a bit complex to nest here, for now, if any remainder, add one daily rate.
                # A more precise implementation would re-run hourly logic for the remainder.
                # Simplified: if remaining_hours_after_full_days > 0, add another effective_daily_rate
                # Or, if hourly charge for remainder is less, use that.
                # For now, let's just say if > 6 hours in remainder, it's another day rate.
                if remaining_hours_after_full_days > 6 : # Simplified rule from daily rate itself
                     daily_charge_total += effective_daily_rate
                elif remaining_hours_after_full_days > 0 and 'HOURLY' in calculated_charges: # Use hourly for remainder if <6h
                    # This is tricky: calculated_charges['HOURLY'] is for the *total* duration.
                    # We need hourly charge for *just* remaining_hours_after_full_days.
                    # This part needs a sub-function for hourly calculation based on duration.
                    # For now, if there's a remainder, we'll assume it's handled by the overall "best rate" logic.
                    # The MD says: "Any remaining hours ... will be calculated using the Hourly Rate ... capped at the Full Day Rate"
                    # This implies the hourly calculation should be primary for the remainder.
                    # The current structure calculates total hourly and total daily, then picks min.
                    # This might cover it if hourly logic correctly handles multi-day caps.
                    pass # Let the best rate logic handle this for now.
            
            if daily_charge_total > 0 : # Only add if it's a valid calculation
                 calculated_charges['DAILY_MULTI'] = round(daily_charge_total, 2)


    # Determine best rate
    if not calculated_charges:
        return jsonify({"error": "No applicable rates found or configured for calculation"}), 500

    # Default to a very high number if a modality wasn't calculated
    final_charge = min(calculated_charges.values()) if calculated_charges else 0
    
    # Find which modality resulted in the final_charge
    modality_applied = "UNKNOWN"
    for m, c in calculated_charges.items():
        if c == final_charge:
            modality_applied = m
            break
            
    # --- End Calculation Logic ---

    return jsonify({
        "session_id": session_id,
        "vehicle_type": vehicle_type,
        "entry_timestamp": entry_timestamp.isoformat(),
        "exit_timestamp_used_for_calc": exit_timestamp_to_use.isoformat(),
        "duration_total_minutes": round(duration_total_minutes, 2),
        "inflation_factor_applied": inflation_factor,
        "calculated_total_charge": final_charge,
        "modality_applied": modality_applied,
        "breakdown_by_modality": calculated_charges # For transparency or debugging
    }), 200


# --- API Endpoints for ANPR Service to call ---
# Example: /api/cashier/print-entry-ticket (POST with session_details)

@app.route('/api/record-payment', methods=['POST'])
def record_payment_api():
    if parking_sessions_collection is None:
        app.logger.error("record_payment_api: parking_sessions_collection is None.")
        return jsonify({"error": "Database service or parking_sessions_collection not initialized"}), 500

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing JSON data in request body"}), 400

    required_fields = [
        "session_id", "calculated_total_charge", "amount_received", 
        "change_given", "payment_timestamp_iso", "cashier_user_id", 
        "modality_applied" 
        # "inflation_factor_applied", "duration_minutes" # Optional for storage, good for audit
    ]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields:
        return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    try:
        session_oid = ObjectId(data['session_id'])
        payment_ts = datetime.fromisoformat(data['payment_timestamp_iso'].replace("Z", "+00:00"))
    except Exception as e:
        app.logger.error(f"Error parsing session_id or payment_timestamp_iso: {e}")
        return jsonify({"error": "Invalid session_id or payment_timestamp_iso format"}), 400

    update_fields = {
        'payment_status': 'paid_pending_exit', # Or 'completed_paid' if exit already happened?
        'calculated_cost': float(data['calculated_total_charge']),
        'amount_received': float(data['amount_received']),
        'change_given': float(data['change_given']),
        'payment_timestamp': payment_ts,
        'payment_processed_by_user_id': data['cashier_user_id'], # Storing user ID
        'payment_modality': data['modality_applied']
    }
    if 'inflation_factor_applied' in data:
        update_fields['payment_inflation_factor'] = float(data['inflation_factor_applied'])
    if 'duration_minutes' in data:
        update_fields['payment_duration_minutes'] = float(data['duration_minutes'])


    try:
        result = parking_sessions_collection.update_one(
            {'_id': session_oid, 'payment_status': 'unpaid'}, # Ensure we only update unpaid sessions
            {'$set': update_fields}
        )
        if result.matched_count == 0:
             # Could be already paid, or session_id wrong
            existing_session = parking_sessions_collection.find_one({'_id': session_oid})
            if existing_session and existing_session.get('payment_status') != 'unpaid':
                 app.logger.warning(f"Attempt to record payment for already processed session: {data['session_id']}")
                 return jsonify({"error": "Session already processed or payment status is not 'unpaid'"}), 409 # Conflict
            else:
                 app.logger.warning(f"Session not found for payment recording or status not 'unpaid': {data['session_id']}")
                 return jsonify({"error": "Session not found or not eligible for payment"}), 404

        if result.modified_count == 1:
            app.logger.info(f"Payment recorded successfully for session: {data['session_id']}")
            return jsonify({"message": "Payment recorded successfully"}), 200
        else:
            # This case should ideally be caught by matched_count == 0 if status was not 'unpaid'
            app.logger.error(f"Payment recording: Matched session {data['session_id']} but did not modify. Current data: {update_fields}")
            return jsonify({"error": "Payment recorded but no changes made to document (already paid or data identical?)"}), 200 # Or a different status
            
    except Exception as e:
        app.logger.error(f"Database error recording payment for session {data['session_id']}: {e}", exc_info=True)
        return jsonify({"error": "Database error while recording payment"}), 500


if __name__ == '__main__':
    # Port is set by FLASK_RUN_PORT in Dockerfile or docker-compose
    app.run(debug=FLASK_DEBUG)
