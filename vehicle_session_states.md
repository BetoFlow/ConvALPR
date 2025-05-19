# Vehicle Parking Session States

This document outlines the lifecycle and possible states of a vehicle's parking session within the system. The session lifecycle is managed by a formal state machine, `ParkingSessionStateMachine`, located in the `common/session_state_machine.py` module.

The defined states in the FSM are: `INIT`, `VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`, `PAID_AWAITING_EXIT`, `SESSION_UNPAID_TIMEOUT`, and `SESSION_CLOSED`.

## 1. `INIT` (Initial State)

*   **Description:** A placeholder state before a session is formally created or recognized by an entry event. Not typically persisted as the primary state for active sessions.
*   **Trigger:** System initialization for a potential session.
*   **Next Possible State(s):** `VEHICLE_ENTERED` (upon `event_detect_entry`).

## 2. `VEHICLE_ENTERED`

*   **Description:** A vehicle is detected entering the parking facility, and a new parking session is initiated.
*   **Trigger:** `event_detect_entry` from the ALPR service (entry point camera/sensor).
*   **System Actions:**
    *   A new parking session document is created in MongoDB.
    *   Entry timestamp, image, camera ID, plate number, and default vehicle type are recorded.
    *   `payment_status` is set to `unpaid`.
    *   The legacy `status` field is set to `inside`.
*   **Next Possible State(s):**
    *   `AWAITING_PAYMENT_RESOLUTION` (if `event_detect_exit` occurs).
    *   `PAID_AWAITING_EXIT` (if `payment_received` event occurs before exit detection).

*(Note: The `VEHICLE_PARKED` state, previously mentioned as an intermediary state for specific spot occupancy, is not currently implemented in the FSM but could be a future enhancement.)*

## 3. `AWAITING_PAYMENT_RESOLUTION`

*   **Description:** The vehicle has been detected at an exit point (or a common camera acting as an exit point), and the system is awaiting payment or for the grace period to expire.
*   **Trigger:** `event_detect_exit` from the ALPR service, when the session was in `VEHICLE_ENTERED`.
*   **System Actions:**
    *   Exit timestamp, image, and camera ID are recorded.
    *   A grace period timer effectively starts (monitored by the web portal).
    *   The system is ready for payment processing.
*   **Next Possible State(s):**
    *   `SESSION_CLOSED` (if `payment_received` event occurs).
    *   `SESSION_UNPAID_TIMEOUT` (if `event_payment_timeout` occurs, triggered by web portal).

## 4. `PAID_AWAITING_EXIT`

*   **Description:** Payment for the parking session has been successfully received and confirmed while the vehicle is still considered inside (i.e., before an exit detection event).
*   **Trigger:** `payment_received` event from the Cashier service, when the session was in `VEHICLE_ENTERED`.
*   **System Actions:**
    *   Payment details (amount, method, transaction ID, etc.) are recorded.
    *   `payment_status` is updated to `completed_paid`.
    *   The legacy `status` field remains `inside`.
*   **Next Possible State(s):** `SESSION_CLOSED` (if `event_detect_exit` occurs).

## 5. `SESSION_UNPAID_TIMEOUT`

*   **Description:** The grace period initiated after exit detection (`AWAITING_PAYMENT_RESOLUTION` state) has expired, and no payment confirmation has been received.
*   **Trigger:** `event_payment_timeout` (typically triggered by the Web Portal checking grace period expiry).
*   **System Actions:**
    *   `payment_status` is updated to `unpaid_timeout`.
    *   The legacy `status` field is updated to `exited`.
    *   Further actions (invoicing, alerts) can be based on this state.
*   **Next Possible State(s):** `SESSION_CLOSED` (if `payment_received` event occurs - allowing late payment, or via `event_force_close_unpaid`).

## 6. `SESSION_CLOSED`

*   **Description:** The parking session is fully concluded.
*   **Triggers:**
    *   `event_detect_exit` when the session was in `PAID_AWAITING_EXIT`.
    *   `payment_received` event when the session was in `AWAITING_PAYMENT_RESOLUTION` or `SESSION_UNPAID_TIMEOUT`.
    *   `event_force_close_unpaid` when the session was in `SESSION_UNPAID_TIMEOUT`.
*   **System Actions:**
    *   All session data is finalized (final duration, total fee, payment status, entry/exit times).
    *   The legacy `status` field is set to `exited`.
    *   The session record is effectively archived for reporting and auditing.
    *   No further state changes are expected for this session via the primary FSM events.
*   **Next Possible State(s):** None (Terminal state).

---

This state model, implemented via `ParkingSessionStateMachine`, provides a clear and centralized flow for managing vehicle sessions from entry to exit, including payment and timeout scenarios.
