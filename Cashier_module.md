## **Parking System Cashier Module Specifications: Payment Modalities & Vehicle Classification (Inflation-Adjusted & UI-Driven)**

Document Version: 1.4
Date: May 19, 2025
Prepared For: AI Cashier Module Development Team

### ---

**1\. Introduction**

This document outlines the core logic for calculating parking charges within the cashier module, with a critical focus on **dynamic rate adjustments to counter inflation and currency devaluation**. The system must accurately determine the amount due based on time parked, chosen payment modality, and vehicle type, ensuring profitability in a volatile economic environment. It must also handle fractional time calculations and manage different payment options. Session state transitions related to payments are managed by interacting with the central `ParkingSessionStateMachine`.

### **2\. Core Concepts & Definitions**

* **Entry Event:** Timestamp when a vehicle enters the parking facility.
* **Exit Event:** Timestamp when a vehicle exits or is detected at an exit point.
* **Parking Duration:** Calculated difference between Exit Event (or current time if not exited) and Entry Event.
* **Payment Modality:** The specific pricing structure applied (hourly, daily, monthly, etc.).
* **Vehicle Type:** Classification of the vehicle (Car/SUV, Motorcycle, Truck).
* **Inflation Adjustment Factor:** A multiplier applied to base rates to account for inflation, to be updated regularly via the web interface.
* **Cashier Module Role:** To receive parking session details, calculate the total charge using current (inflation-adjusted) rates for various modalities, and upon payment, trigger a `payment_received` event on the `ParkingSessionStateMachine`. The state machine then updates the session's state and persists payment details.
* **`ParkingSessionStateMachine`:** A central state machine (defined in `common/session_state_machine.py`) that governs the lifecycle of a parking session. Services like ALPR, Cashier, and Web Portal interact with it by triggering events.

### **3\. Vehicle Classification**

The system must support the following primary vehicle classifications for pricing. These are distinct categories based on size and space requirements within a parking facility.

* **Code:** CAR\_SUV
  * **Description:** Standard automobiles (sedans, hatchbacks), and common SUVs (Sport Utility Vehicles), and typically smaller "camionetas" or crossovers that fit into a standard parking space.
  * **Default Assignment:** If vehicle type is not automatically detected upon entry, new parking sessions created by the `anpr-service` (via the FSM) default to this type (`CAR_SUV`). This can be manually changed later via the web portal if needed.
* **Code:** TRUCK
  * **Description:** Larger pickup trucks, vans, and light-to-medium duty trucks that require more space or a different maneuvering radius. This category includes vehicles often referred to as "camiones" (trucks) or larger "camionetas" that may exceed standard car dimensions.
  * **Pricing Implication:** Generally charged at a higher rate than CAR\_SUV.
* **Code:** MOTORCYCLE
  * **Description:** Two-wheeled motor vehicles. These vehicles occupy significantly less space.
  * **Pricing Implication:** Typically charged at a lower rate than CAR\_SUV.

**Future Consideration:** The system should be designed to easily add more granular vehicle types (e.g., BICYCLE, HEAVY\_TRUCK, BUS) if required in the future, without major re-architecting of the core pricing logic.

### **4\. Payment Modalities (Adjusted by Inflation Factor)**

The system must support various payment modalities. The cashier module will receive the parking duration and vehicle type, and apply the most favorable (lowest) calculated rate for the customer, unless a specific modality (e.g., Monthly Abono) is pre-selected.

All **base prices** will be configured in Argentine Pesos (ARS). The system will then apply the current **Inflation Adjustment Factor** to these base prices at the point of calculation.

#### **Critical Requirement: Inflation Adjustment Factor**

* **Mechanism:** The system must include a global, configurable INFLATION\_ADJUSTMENT\_FACTOR (e.g., 1.0, 1.03, 1.055).
* **Update Frequency:** This factor **must be easily settable via a dedicated section in the web interface (UI)** by an administrator/owner, ideally **once per month**.
* **Application:** All base rates (hourly, daily, nightly, long-term, and potentially monthly abono if not separately indexed) will be multiplied by this factor at the time of calculation or display.

**Example:** If Base\_Rate\_Per\_Hour\_CAR\_SUV is ARS 7,000 and INFLATION\_ADJUSTMENT\_FACTOR is 1.03 (representing a 3% monthly adjustment), the effective rate will be ARS 7,000 \* 1.03 \= ARS 7,210.

#### **4.1. Hourly Rate (Tarifa por Hora)**
(Content remains the same as original)
* **Description:** Primary rate for short-term parking.
* **Calculation Logic:**
  * **First Hour:** Charged as a full hour, regardless of actual minutes parked.
  * **Subsequent Time:** Calculated in **30-minute fractions**. Any portion of a 30-minute block will be rounded up to the full 30-minute block.
  * **Max Daily Limit:** If the accumulated hourly rate exceeds the **Full Day Rate**, the system should automatically cap the charge at the Full Day Rate for that 24-hour period.
* **Base Rates (Example \- to be configured dynamically):**
  * CAR\_SUV\_BASE\_HOURLY\_RATE: ARS 7,000
  * TRUCK\_BASE\_HOURLY\_RATE: ARS 10,000
  * MOTORCYCLE\_BASE\_HOURLY\_RATE: ARS 3,500
* **Effective Rate Calculation:** Effective\_Hourly\_Rate \= BASE\_HOURLY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

#### **4.2. Full Day Rate (Estadía Completa / Tarifa por Día)**
(Content remains the same as original)
* **Description:** A fixed rate for parking for a full 24-hour period.
* **Calculation Logic:**
  * If parking duration is **greater than 6 hours** and **less than or equal to 24 hours**, apply this fixed rate.
  * **Multi-Day Stays:** For durations exceeding 24 hours, the Full Day Rate is applied for each full 24-hour block. Any remaining hours beyond the last full 24-hour block will be calculated using the **Hourly Rate with fraction logic**, but capped at the Full Day Rate for that partial day if the accumulated hourly rate exceeds it.
* **Base Rates (Example \- to be configured dynamically):**
  * CAR\_SUV\_BASE\_DAILY\_RATE: ARS 47,500
  * TRUCK\_BASE\_DAILY\_RATE: ARS 70,000
  * MOTORCYCLE\_BASE\_DAILY\_RATE: ARS 23,750
* **Effective Rate Calculation:** Effective\_Daily\_Rate \= BASE\_DAILY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

#### **4.3. Monthly Abono (Abono Mensual / Cochera Fija)**
(Content remains the same as original)
* **Description:** A pre-paid fixed monthly fee for unlimited parking access for a specific vehicle.
* **Calculation Logic:**
  * This modality is handled **outside the real-time duration calculation**.
  * The cashier module needs a way to **identify a vehicle as a Monthly Abono holder** (e.g., via license plate lookup, RFID tag, or pre-assigned monthly pass ID).
  * If identified as an Abono holder, the charge is **ARS 0 (zero)** for the specific parking instance, as the monthly fee is already paid.
  * **Important:** The system should still log the entry/exit for occupancy tracking and reporting. The session state will still transition (e.g. `VEHICLE_ENTERED` -> `PAID_AWAITING_EXIT` if abono status is checked at payment time, or directly to `SESSION_CLOSED` if abono status means it's always considered "paid"). This interaction needs to be defined with the FSM. *For now, assume abono check happens before FSM payment event.*
* **Base Rates (Example \- to be configured dynamically):**
  * CAR\_SUV\_BASE\_MONTHLY\_ABONO: ARS 80,000
  * TRUCK\_BASE\_MONTHLY\_ABONO: ARS 120,000
  * MOTORCYCLE\_BASE\_MONTHLY\_ABONO: ARS 40,000
  * **Inflation Adjustment for Abono:** While the *charge* for a single parking event is zero, the *base monthly fee* for the abono itself **should also be subject to the INFLATION\_ADJUSTMENT\_FACTOR** if new abonos are issued or existing ones are renewed. The admin UI should allow for **monthly adjustment of these base abono rates** as well. This is crucial for the owner's profitability.

#### **4.4. Nightly Rate (Estadía Nocturna) \- Optional/Configurable**
(Content remains the same as original)
* **Description:** A reduced fixed rate for parking during specific overnight hours.
* **Calculation Logic:** (As per previous version)
* **Base Rates (Example \- to be configured dynamically):**
  * CAR\_SUV\_BASE\_NIGHTLY\_RATE: ARS 25,000
  * TRUCK\_BASE\_NIGHTLY\_RATE: ARS 35,000
  * MOTORCYCLE\_BASE\_NIGHTLY\_RATE: ARS 12,500
* **Effective Rate Calculation:** Effective\_Nightly\_Rate \= BASE\_NIGHTLY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

#### **4.5. Long-Term / Extended Stay Rates (for \> 1 day) \- Optional/Configurable**
(Content remains the same as original)
* **Description:** Discounted daily rates for stays exceeding a certain number of days.
* **Calculation Logic:** (As per previous version)
* **Base Rates (Example \- to be configured dynamically):**
  * CAR\_SUV\_BASE\_LONGTERM\_DAILY\_RATE: ARS 45,000
  * TRUCK_BASE\_LONGTERM\_DAILY\_RATE: ARS 65,000
  * MOTORCYCLE\_BASE\_LONGTERM\_DAILY_RATE: ARS 22,500
* **Effective Rate Calculation:** Effective\_LongTerm\_Daily\_Rate \= BASE\_LONGTERM\_DAILY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

### **5\. Charge Calculation Algorithm Flow (Cashier Module Logic)**
(The core calculation algorithm remains the same. The FSM integration affects how the *result* of payment is handled, not the calculation itself.)
1. **Receive Inputs:**
   * Session ID (to fetch Entry\_Timestamp, Vehicle\_Type, current `session_state` from DB).
   * (Optional) Manual\_Exit\_Timestamp for "price so far" calculations.
2. **Retrieve Current INFLATION\_ADJUSTMENT\_FACTOR:**
   * Access the globally configured value.
3. **Check for Monthly Abono:** (This logic might be handled before calling calculate-charge, or as part of it)
   * Lookup Vehicle\_License\_Plate.
   * If Abono holder, Total\_Charge \= 0.
4. **Calculate Parking Duration:**
   * Based on session's Entry\_Timestamp and relevant Exit\_Timestamp (actual or manual).
5. **Evaluate Best Rate:** (As per original detailed logic)
   * Calculate charges for all applicable modalities (Hourly, Daily, etc.) using inflation-adjusted rates.
   * Determine the minimum charge.
6. **Final Charge:**
   * Total\_Charge \= Min\_Charge.
7. **Return Calculated Information:**
   * Provide session details, duration, inflation factor, total charge, modality applied, and breakdown to the caller (e.g., Web Portal UI).

### **6\. Payment Processing Assistant**

Once the Total\_Charge is calculated and presented (e.g., in the Web Portal UI):

* **Receive Payment Confirmation:** The Cashier Service's `/api/record-payment` endpoint is called with session ID, payment details (amount received, charge, change, cashier ID, etc.).
* **Trigger State Machine Event:**
    * The service loads the `ParkingSessionStateMachine` for the given session.
    * It verifies if a payment event is valid for the current session state (e.g., using `machine.get_triggers()`).
    * It triggers the `payment_received` event on the state machine, passing along all payment details.
* **State Machine Handles Update:**
    * The `ParkingSessionStateMachine` transitions the session to the appropriate next state (`PAID_AWAITING_EXIT` or `SESSION_CLOSED`).
    * Its callbacks persist the new state and all payment-related information (amount, change, timestamp, user ID, modality) to the MongoDB session document.
* **Return Result:** The Cashier Service API endpoint returns a success or failure message based on the outcome of the FSM event trigger.

### **7\. Configuration & Dynamic Rates (UI-Driven)**
(Content largely the same, emphasizing that these are settings for the calculation logic, not direct FSM parameters)
* All **base rates** and the **INFLATION\_ADJUSTMENT\_FACTOR** are managed via the Web Portal UI and stored such that the Cashier Service can access them for calculations.

#### **Key Inflation Adjustment Mechanism (Web UI Configuration)**
(Content remains the same as original)

#### **Additional System Settings (Configurable via Admin UI)**
(Content remains the same as original, these settings are used by various services including Cashier for calculations or ALPR for its logic)
*   **Operational Timezone**
*   **Plate Detection Cooldown**
*   **Payment Grace Period**

### **8\. System Status & Monitoring (for Cashier)**
(Content remains the same as original)
* **Real-time Occupancy:** Display Total Slots, Occupied Slots, Available Slots.
* **Entry/Exit Log:** Show recent vehicle movements.
* **Cashier Shift Summary:** View transactions and cash collected.
* **Current Rate Display:** Show effective rates reflecting inflation adjustment.
