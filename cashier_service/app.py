import os
from flask import Flask, jsonify, request
from pymongo import MongoClient
from pymongo.errors import ConnectionFailure
from bson.objectid import ObjectId
import logging
from datetime import datetime

from common.session_state_machine import ParkingSessionStateMachine # Simplified import


app = Flask(__name__)

# Configuration from environment variables
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://mongodb:27017/')
DB_NAME = os.environ.get('DB_NAME', 'anpr_db')
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', '0').lower() in ['true', '1', 't']

if FLASK_DEBUG:
    app.logger.setLevel(logging.DEBUG)
else:
    app.logger.setLevel(logging.INFO)

client = None
db = None
RATE_CONFIGS_COLLECTION_NAME = 'rate_configs'
ABONO_HOLDERS_COLLECTION_NAME = 'abono_holders'
INFLATION_FACTORS_COLLECTION_NAME = 'inflation_factors'

rate_configs_collection = None
abono_holders_collection = None
inflation_factors_collection = None
parking_sessions_collection = None

try:
    client = MongoClient(MONGO_URI)
    client.admin.command('ismaster') 
    db = client[DB_NAME]
    
    rate_configs_collection = db[RATE_CONFIGS_COLLECTION_NAME]
    abono_holders_collection = db[ABONO_HOLDERS_COLLECTION_NAME]
    inflation_factors_collection = db[INFLATION_FACTORS_COLLECTION_NAME]
    parking_sessions_collection = db['parking_sessions']

    app.logger.info(f"Cashier Service: Successfully connected to MongoDB at {MONGO_URI}, DB: {DB_NAME}")
except ConnectionFailure:
    app.logger.error(f"Cashier Service: MongoDB connection failed at {MONGO_URI}.")
except Exception as e:
    app.logger.error(f"Cashier Service: An error occurred during MongoDB initialization: {e}", exc_info=True)


@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "service": "cashier-service"}), 200

INFLATION_FACTOR_DOC_ID = "current_inflation_factor"

@app.route('/api/inflation-factor', methods=['GET'])
def get_inflation_factor():
    if inflation_factors_collection is None:
        return jsonify({"error": "Database not connected"}), 500
    factor_doc = inflation_factors_collection.find_one({"_id": INFLATION_FACTOR_DOC_ID})
    if factor_doc:
        return jsonify({"factor": factor_doc.get("factor", 1.0), "last_updated": factor_doc.get("last_updated")}), 200
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
        if new_factor <= 0: return jsonify({"error": "Factor must be a positive number"}), 400
    except ValueError: return jsonify({"error": "Factor must be a valid number"}), 400
    update_result = inflation_factors_collection.update_one(
        {"_id": INFLATION_FACTOR_DOC_ID},
        {"$set": {"factor": new_factor, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    if update_result.acknowledged:
        app.logger.info(f"Inflation factor updated to: {new_factor}")
        return jsonify({"message": "Inflation factor updated successfully", "new_factor": new_factor}), 200
    app.logger.error("Failed to update inflation factor in DB.") 
    return jsonify({"error": "Failed to update inflation factor"}), 500

OPERATIONAL_TIMEZONE_DOC_ID = "operational_timezone_config"
DEFAULT_OPERATIONAL_TIMEZONE = "UTC"

@app.route('/api/settings/timezone', methods=['GET'])
def get_operational_timezone():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    tz_doc = inflation_factors_collection.find_one({"_id": OPERATIONAL_TIMEZONE_DOC_ID})
    if tz_doc and tz_doc.get("timezone"):
        return jsonify({"timezone": tz_doc.get("timezone"), "last_updated": tz_doc.get("last_updated")}), 200
    return jsonify({"timezone": DEFAULT_OPERATIONAL_TIMEZONE, "last_updated": None, "message": "Timezone not set, returning default UTC."}), 200

@app.route('/api/settings/timezone', methods=['POST'])
def set_operational_timezone():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    data = request.get_json();
    if not data or 'timezone' not in data: return jsonify({"error": "Missing 'timezone' in request body"}), 400
    new_timezone = data['timezone']
    update_result = inflation_factors_collection.update_one(
        {"_id": OPERATIONAL_TIMEZONE_DOC_ID},
        {"$set": {"timezone": new_timezone, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    if update_result.acknowledged:
        app.logger.info(f"Operational timezone updated to: {new_timezone}")
        return jsonify({"message": "Operational timezone updated successfully", "new_timezone": new_timezone}), 200
    app.logger.error("Failed to update operational timezone in DB.")
    return jsonify({"error": "Failed to update operational timezone"}), 500

PLATE_COOLDOWN_CONFIG_DOC_ID = "plate_cooldown_config"
DEFAULT_PLATE_COOLDOWN_SECONDS = 300

@app.route('/api/settings/plate-cooldown', methods=['GET'])
def get_plate_cooldown():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    cooldown_doc = inflation_factors_collection.find_one({"_id": PLATE_COOLDOWN_CONFIG_DOC_ID})
    if cooldown_doc and "cooldown_seconds" in cooldown_doc:
        return jsonify({"cooldown_seconds": cooldown_doc.get("cooldown_seconds"), "last_updated": cooldown_doc.get("last_updated")}), 200
    return jsonify({"cooldown_seconds": DEFAULT_PLATE_COOLDOWN_SECONDS, "last_updated": None, "message": "Cooldown not set, returning default."}), 200

@app.route('/api/settings/plate-cooldown', methods=['POST'])
def set_plate_cooldown():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    data = request.get_json()
    if not data or 'cooldown_seconds' not in data: return jsonify({"error": "Missing 'cooldown_seconds' in request body"}), 400
    try:
        new_cooldown = int(data['cooldown_seconds'])
        if new_cooldown < 0: return jsonify({"error": "Cooldown must be a non-negative integer"}), 400
    except ValueError: return jsonify({"error": "Cooldown must be a valid integer"}), 400
    update_result = inflation_factors_collection.update_one(
        {"_id": PLATE_COOLDOWN_CONFIG_DOC_ID},
        {"$set": {"cooldown_seconds": new_cooldown, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    if update_result.acknowledged:
        app.logger.info(f"Plate detection cooldown updated to: {new_cooldown} seconds")
        return jsonify({"message": "Plate cooldown updated successfully", "new_cooldown_seconds": new_cooldown}), 200
    app.logger.error("Failed to update plate cooldown in DB.")
    return jsonify({"error": "Failed to update plate cooldown"}), 500

GRACE_PERIOD_CONFIG_DOC_ID = "grace_period_config"
DEFAULT_GRACE_PERIOD_SECONDS = 300

@app.route('/api/settings/grace-period', methods=['GET'])
def get_grace_period():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    grace_doc = inflation_factors_collection.find_one({"_id": GRACE_PERIOD_CONFIG_DOC_ID})
    if grace_doc and "grace_period_seconds" in grace_doc:
        return jsonify({"grace_period_seconds": grace_doc.get("grace_period_seconds"), "last_updated": grace_doc.get("last_updated")}), 200
    return jsonify({"grace_period_seconds": DEFAULT_GRACE_PERIOD_SECONDS, "last_updated": None, "message": "Grace period not set, returning default."}), 200

@app.route('/api/settings/grace-period', methods=['POST'])
def set_grace_period():
    if inflation_factors_collection is None: return jsonify({"error": "Database not connected"}), 500
    data = request.get_json()
    if not data or 'grace_period_seconds' not in data: return jsonify({"error": "Missing 'grace_period_seconds' in request body"}), 400
    try:
        new_grace_period = int(data['grace_period_seconds'])
        if new_grace_period < 0: return jsonify({"error": "Grace period must be a non-negative integer"}), 400
    except ValueError: return jsonify({"error": "Grace period must be a valid integer"}), 400
    update_result = inflation_factors_collection.update_one(
        {"_id": GRACE_PERIOD_CONFIG_DOC_ID},
        {"$set": {"grace_period_seconds": new_grace_period, "last_updated": datetime.utcnow()}},
        upsert=True
    )
    if update_result.acknowledged:
        app.logger.info(f"Grace period updated to: {new_grace_period} seconds")
        return jsonify({"message": "Grace period updated successfully", "new_grace_period_seconds": new_grace_period}), 200
    app.logger.error("Failed to update grace period in DB.")
    return jsonify({"error": "Failed to update grace period"}), 500

VEHICLE_TYPES_SUPPORTED = {"CAR_SUV": "Car/SUV", "TRUCK": "Truck/Van", "MOTORCYCLE": "Motorcycle"}

@app.route('/api/rates', methods=['GET'])
def get_all_rates():
    if rate_configs_collection is None: return jsonify({"error": "Database not connected"}), 500
    rates_data = {doc['_id']: doc for doc in rate_configs_collection.find({})}
    for vt_code, vt_label in VEHICLE_TYPES_SUPPORTED.items():
        if vt_code not in rates_data:
            rates_data[vt_code] = {"_id": vt_code, "vehicle_type_label": vt_label, "rates": {}, "abono_monthly_base_fee": 0, "last_updated": None, "message": "Default placeholder - rates not configured yet."}
    return jsonify(rates_data), 200

@app.route('/api/rates/<vehicle_type_code>', methods=['GET'])
def get_rates_for_vehicle_type(vehicle_type_code):
    if rate_configs_collection is None: return jsonify({"error": "Database not connected"}), 500
    if vehicle_type_code not in VEHICLE_TYPES_SUPPORTED: return jsonify({"error": f"Unsupported vehicle type: {vehicle_type_code}"}), 400
    rates_doc = rate_configs_collection.find_one({"_id": vehicle_type_code})
    if rates_doc: return jsonify(rates_doc), 200
    return jsonify({"_id": vehicle_type_code, "vehicle_type_label": VEHICLE_TYPES_SUPPORTED[vehicle_type_code], "rates": {}, "abono_monthly_base_fee": 0, "last_updated": None, "message": "Rates not configured for this vehicle type."}), 200

@app.route('/api/rates/<vehicle_type_code>', methods=['POST'])
def update_rates_for_vehicle_type(vehicle_type_code):
    if rate_configs_collection is None: return jsonify({"error": "Database not connected"}), 500
    if vehicle_type_code not in VEHICLE_TYPES_SUPPORTED: return jsonify({"error": f"Unsupported vehicle type: {vehicle_type_code}"}), 400
    data = request.get_json()
    if not data: return jsonify({"error": "Missing data in request body"}), 400
    if not isinstance(data.get('rates'), dict) or not isinstance(data.get('abono_monthly_base_fee'), (int, float)):
         return jsonify({"error": "Invalid data structure for rates or abono_monthly_base_fee"}), 400
    update_payload = {"vehicle_type_label": VEHICLE_TYPES_SUPPORTED[vehicle_type_code], "rates": data.get('rates', {}), "abono_monthly_base_fee": data.get('abono_monthly_base_fee', 0), "last_updated": datetime.utcnow()}
    update_result = rate_configs_collection.update_one({"_id": vehicle_type_code}, {"$set": update_payload}, upsert=True)
    if update_result.acknowledged:
        app.logger.info(f"Rates updated for vehicle type: {vehicle_type_code}")
        return jsonify({"message": f"Rates for {vehicle_type_code} updated successfully"}), 200
    app.logger.error(f"Failed to update rates for {vehicle_type_code} in DB.")
    return jsonify({"error": f"Failed to update rates for {vehicle_type_code}"}), 500

@app.route('/api/calculate-charge', methods=['POST'])
def calculate_charge_api():
    if parking_sessions_collection is None or rate_configs_collection is None or inflation_factors_collection is None:
        app.logger.error("calculate_charge_api: One or more critical collections are None.")
        return jsonify({"error": "Database service or critical collections not initialized"}), 500
    data = request.get_json()
    if not data: return jsonify({"error": "Missing JSON data in request body"}), 400
    session_id = data.get('session_id'); manual_exit_timestamp_str = data.get('manual_exit_timestamp') 
    if not session_id: return jsonify({"error": "Missing 'session_id' in request"}), 400
    try: session_oid = ObjectId(session_id)
    except Exception: return jsonify({"error": "Invalid session_id format"}), 400
    session = parking_sessions_collection.find_one({"_id": session_oid})
    if not session: return jsonify({"error": "Parking session not found"}), 404
    vehicle_type = session.get('vehicle_type')
    if not vehicle_type or vehicle_type not in VEHICLE_TYPES_SUPPORTED:
        return jsonify({"error": f"Vehicle type not set or invalid for session {session_id}. Please set it first."}), 400
    entry_timestamp = session.get('entry_timestamp')
    if not entry_timestamp: return jsonify({"error": "Session has no entry timestamp"}), 400
    exit_timestamp_to_use = None
    if manual_exit_timestamp_str:
        try: exit_timestamp_to_use = datetime.fromisoformat(manual_exit_timestamp_str.replace("Z", "+00:00"))
        except ValueError: return jsonify({"error": "Invalid manual_exit_timestamp format. Use ISO format."}), 400
    elif session.get('status') == 'exited' and session.get('exit_timestamp'): exit_timestamp_to_use = session['exit_timestamp']
    else: exit_timestamp_to_use = datetime.utcnow()
    factor_doc = inflation_factors_collection.find_one({"_id": INFLATION_FACTOR_DOC_ID})
    inflation_factor = factor_doc.get("factor", 1.0) if factor_doc else 1.0
    rates_doc = rate_configs_collection.find_one({"_id": vehicle_type})
    if not rates_doc or not rates_doc.get('rates'):
        return jsonify({"error": f"Rate configuration not found for vehicle type: {vehicle_type}"}), 404
    vehicle_rates = rates_doc['rates']
    duration_delta = exit_timestamp_to_use - entry_timestamp
    duration_total_minutes = duration_delta.total_seconds() / 60
    duration_total_hours = duration_total_minutes / 60
    if duration_total_minutes < 0: return jsonify({"error": "Exit time is before entry time"}), 400
    calculated_charges = {}
    hourly_rate_config = vehicle_rates.get('HOURLY')
    if hourly_rate_config and 'base_value' in hourly_rate_config:
        base_hourly = float(hourly_rate_config['base_value']); effective_hourly_rate = base_hourly * inflation_factor
        fraction_minutes = int(hourly_rate_config.get('fraction_minutes', 30)); first_hour_full = hourly_rate_config.get('first_hour_full', True)
        if duration_total_minutes <= 0: hourly_charge = 0
        elif first_hour_full and duration_total_minutes <= 60: hourly_charge = effective_hourly_rate
        else:
            chargeable_hours = 0
            if first_hour_full:
                chargeable_hours = 1
                if duration_total_minutes > 60:
                    remaining_minutes_after_first_hour = duration_total_minutes - 60
                    num_fractions = (remaining_minutes_after_first_hour + fraction_minutes -1) // fraction_minutes
                    chargeable_hours += num_fractions * (fraction_minutes / 60.0)
            else:
                num_fractions = (duration_total_minutes + fraction_minutes -1) // fraction_minutes
                chargeable_hours = num_fractions * (fraction_minutes / 60.0)
            hourly_charge = chargeable_hours * effective_hourly_rate
        daily_rate_config_for_cap = vehicle_rates.get('DAILY')
        if daily_rate_config_for_cap and 'base_value' in daily_rate_config_for_cap:
            effective_daily_rate_for_cap = float(daily_rate_config_for_cap['base_value']) * inflation_factor
            if hourly_charge > effective_daily_rate_for_cap: hourly_charge = effective_daily_rate_for_cap
        calculated_charges['HOURLY'] = round(hourly_charge, 2)
    daily_rate_config = vehicle_rates.get('DAILY')
    if daily_rate_config and 'base_value' in daily_rate_config:
        base_daily = float(daily_rate_config['base_value']); effective_daily_rate = base_daily * inflation_factor
        if 6 < duration_total_hours <= 24: calculated_charges['DAILY'] = round(effective_daily_rate, 2)
        elif duration_total_hours > 24:
            num_full_24h_blocks = int(duration_total_hours // 24); remaining_hours_after_full_days = duration_total_hours % 24
            daily_charge_total = num_full_24h_blocks * effective_daily_rate
            if remaining_hours_after_full_days > 6 : daily_charge_total += effective_daily_rate
            # elif remaining_hours_after_full_days > 0 and 'HOURLY' in calculated_charges: pass # More complex logic needed here
            if daily_charge_total > 0 : calculated_charges['DAILY_MULTI'] = round(daily_charge_total, 2)
    if not calculated_charges: return jsonify({"error": "No applicable rates found or configured for calculation"}), 500
    final_charge = min(calculated_charges.values()) if calculated_charges else 0
    modality_applied = next((m for m, c in calculated_charges.items() if c == final_charge), "UNKNOWN")
    return jsonify({"session_id": session_id, "vehicle_type": vehicle_type, "entry_timestamp": entry_timestamp.isoformat(), "exit_timestamp_used_for_calc": exit_timestamp_to_use.isoformat(), "duration_total_minutes": round(duration_total_minutes, 2), "inflation_factor_applied": inflation_factor, "calculated_total_charge": final_charge, "modality_applied": modality_applied, "breakdown_by_modality": calculated_charges }), 200

@app.route('/api/record-payment', methods=['POST'])
def record_payment_api():
    if parking_sessions_collection is None:
        app.logger.error("record_payment_api: parking_sessions_collection is None.")
        return jsonify({"error": "Database service or parking_sessions_collection not initialized"}), 500

    data = request.get_json()
    if not data: return jsonify({"error": "Missing JSON data in request body"}), 400

    required_fields = ["session_id", "calculated_total_charge", "amount_received", "change_given", "payment_timestamp_iso", "cashier_user_id", "modality_applied"]
    missing_fields = [field for field in required_fields if field not in data]
    if missing_fields: return jsonify({"error": f"Missing required fields: {', '.join(missing_fields)}"}), 400

    try:
        session_oid_str = data['session_id'] # Keep as string for FSM load_session
        payment_ts = datetime.fromisoformat(data['payment_timestamp_iso'].replace("Z", "+00:00"))
    except Exception as e:
        app.logger.error(f"Error parsing session_id or payment_timestamp_iso: {e}")
        return jsonify({"error": "Invalid session_id or payment_timestamp_iso format"}), 400

    session_fsm = ParkingSessionStateMachine.load_session(
        session_id_str=session_oid_str,
        mongo_collection=parking_sessions_collection
    )

    if not session_fsm:
        # load_session logs error already
        return jsonify({"error": "Session not found or FSM could not be loaded"}), 404

    # Prepare payment information for the FSM event
    payment_info_for_fsm = {
        'calculated_cost': float(data['calculated_total_charge']),
        'amount_received': float(data['amount_received']),
        'change_given': float(data['change_given']),
        'payment_timestamp': payment_ts,
        'payment_processed_by_user_id': data['cashier_user_id'],
        'payment_modality': data['modality_applied']
    }
    if 'inflation_factor_applied' in data:
        payment_info_for_fsm['payment_inflation_factor'] = float(data['inflation_factor_applied'])
    if 'duration_minutes' in data:
        payment_info_for_fsm['payment_duration_minutes'] = float(data['duration_minutes'])

    try:
        # Check if payment is allowed based on current FSM state
        # Get valid triggers for the current state and check if 'payment_received' is among them
        current_state_triggers = session_fsm.machine.get_triggers(session_fsm.state)
        if 'payment_received' not in current_state_triggers:
            app.logger.warning(f"Payment recording: Session {session_oid_str} in state {session_fsm.state} cannot process 'payment_received' event. Valid triggers: {current_state_triggers}")
            return jsonify({"error": f"Session not eligible for payment in current state: {session_fsm.state}"}), 409 # Conflict
            
        # Trigger the payment event. FSM handles state change and persistence.
        success = session_fsm.trigger_event('payment_received', payment_info=payment_info_for_fsm)

        if success:
            app.logger.info(f"Payment recorded successfully via FSM for session: {session_oid_str}. New state: {session_fsm.state}")
            return jsonify({"message": "Payment recorded successfully", "new_session_state": session_fsm.state}), 200
        else:
            # FSM trigger_event logs errors internally if transition failed
            app.logger.error(f"Payment recording: FSM event 'payment_received' failed for session {session_oid_str}.")
            return jsonify({"error": "Failed to process payment event via state machine"}), 500
            
    except Exception as e:
        app.logger.error(f"Error during FSM payment processing for session {session_oid_str}: {e}", exc_info=True)
        return jsonify({"error": "Error processing payment via state machine"}), 500

if __name__ == '__main__':
    app.run(debug=FLASK_DEBUG)
