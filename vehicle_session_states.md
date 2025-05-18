# Vehicle Parking Session States

This document outlines the lifecycle and possible states of a vehicle's parking session within the system.

## 1. `VEHICLE_ENTERED`

*   **Description:** A vehicle is detected entering the parking facility.
*   **Trigger:** Detection by an entry point camera or sensor.
*   **System Actions:**
    *   A new parking session is initiated for the detected vehicle (e.g., identified by its license plate).
    *   The entry timestamp is recorded.
    *   Vehicle details (license plate, possibly vehicle type if identifiable) are associated with the session.
*   **Next Possible State(s):** `VEHICLE_PARKED`, `VEHICLE_EXIT_DETECTED`

## 2. `VEHICLE_PARKED`

*   **Description:** The vehicle has occupied a parking space within the facility.
*   **Trigger:**
    *   Implicitly after `VEHICLE_ENTERED` if specific spot monitoring is not implemented.
    *   Explicitly if a parking spot sensor detects occupancy linked to the session.
*   **System Actions:**
    *   (If applicable) The session may be updated with specific parking spot information.
    *   The session duration continues to be tracked.
*   **Next Possible State(s):** `VEHICLE_EXIT_DETECTED`

## 3. `VEHICLE_EXIT_DETECTED`

*   **Description:** The vehicle is detected by an exit point camera or sensor, indicating an intention to leave the facility.
*   **Trigger:** Detection by an exit point camera or sensor.
*   **System Actions:**
    *   The exit detection timestamp is recorded for the session.
    *   The system prepares to calculate the duration and potential fees.
*   **Next Possible State(s):** `AWAITING_PAYMENT_RESOLUTION`

## 4. `AWAITING_PAYMENT_RESOLUTION` (Grace Period / Payment Window)

*   **Description:** After the vehicle is detected at an exit, a defined time window (grace period) is initiated. This allows time for the parking fee to be settled, accommodating scenarios where the driver might be paying at a kiosk, via a mobile app, or at an exit barrier immediately after the camera detection.
*   **Trigger:** Transition from `VEHICLE_EXIT_DETECTED`.
*   **System Actions:**
    *   A timer for the grace period starts.
    *   The system actively monitors for payment confirmation linked to this session.
    *   The total parking duration and applicable fee are calculated.
*   **Next Possible State(s):** `SESSION_PAID_AND_CLOSING`, `SESSION_UNPAID_TIMEOUT`

## 5. `SESSION_PAID_AND_CLOSING`

*   **Description:** Payment for the parking session has been successfully received and confirmed by the system within the allowed timeframe.
*   **Trigger:** Successful payment processing event received for the session (e.g., from a payment gateway, cashier system, or exit barrier confirmation).
*   **System Actions:**
    *   Payment details (amount, method, transaction ID) are recorded against the session.
    *   The session is marked as "Paid."
    *   The system proceeds to finalize and close the session.
*   **Next Possible State(s):** `SESSION_CLOSED`

## 6. `SESSION_UNPAID_TIMEOUT` (Optional State)

*   **Description:** The grace period initiated after `VEHICLE_EXIT_DETECTED` has expired, and no payment confirmation has been received for the session.
*   **Trigger:** Expiry of the grace period timer without a payment confirmation event.
*   **System Actions:**
    *   The session is flagged as "Unpaid."
    *   Depending on configured policies, further actions might be initiated (e.g., generating an invoice, alerting security/staff, adding to a violations list).
    *   The system proceeds to finalize and close the session with an unpaid status.
*   **Next Possible State(s):** `SESSION_CLOSED`

## 7. `SESSION_CLOSED`

*   **Description:** The parking session is fully concluded, either after successful payment or after being handled as unpaid.
*   **Trigger:** Transition from `SESSION_PAID_AND_CLOSING` or `SESSION_UNPAID_TIMEOUT`.
*   **System Actions:**
    *   All session data is finalized (final duration, total fee, payment status, entry/exit times).
    *   The session record is archived for reporting and auditing purposes.
    *   No further financial transactions or state changes are expected for this specific parking instance.
*   **Next Possible State(s):** None (This is a terminal state for the session lifecycle).

---

This state model provides a clear flow for managing vehicle sessions from entry to exit, including the critical payment resolution phase.
