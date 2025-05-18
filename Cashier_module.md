## **Parking System Cashier Module Specifications: Payment Modalities & Vehicle Classification (Inflation-Adjusted & UI-Driven)**

Document Version: 1.3  
Date: May 18, 2025  
Prepared For: AI Cashier Module Development Team

### ---

**1\. Introduction**

This document outlines the core logic for calculating parking charges within the cashier module, with a critical focus on **dynamic rate adjustments to counter inflation and currency devaluation**. The system must accurately determine the amount due based on time parked, chosen payment modality, and vehicle type, ensuring profitability in a volatile economic environment. It must also handle fractional time calculations and manage different payment options.

### **2\. Core Concepts & Definitions**

* **Entry Event:** Timestamp when a vehicle enters the parking facility.  
* **Exit Event:** Timestamp when a vehicle exits the parking facility.  
* **Parking Duration:** Calculated difference between Exit Event and Entry Event.  
* **Payment Modality:** The specific pricing structure applied (hourly, daily, monthly, etc.).  
* **Vehicle Type:** Classification of the vehicle (Car/SUV, Motorcycle, Truck).  
* **Inflation Adjustment Factor:** A multiplier applied to base rates to account for inflation, to be updated regularly via the web interface.  
* **Cashier Module Role:** To receive parking duration, vehicle type, and potentially a chosen modality, calculate the total charge using current (inflation-adjusted) rates, and assist with processing payment and change.

### **3\. Vehicle Classification**

The system must support the following primary vehicle classifications for pricing. These are distinct categories based on size and space requirements within a parking facility.

* **Code:** CAR\_SUV  
  * **Description:** Standard automobiles (sedans, hatchbacks), and common SUVs (Sport Utility Vehicles), and typically smaller "camionetas" or crossovers that fit into a standard parking space.  
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

* **Description:** A pre-paid fixed monthly fee for unlimited parking access for a specific vehicle.  
* **Calculation Logic:**  
  * This modality is handled **outside the real-time duration calculation**.  
  * The cashier module needs a way to **identify a vehicle as a Monthly Abono holder** (e.g., via license plate lookup, RFID tag, or pre-assigned monthly pass ID).  
  * If identified as an Abono holder, the charge is **ARS 0 (zero)** for the specific parking instance, as the monthly fee is already paid.  
  * **Important:** The system should still log the entry/exit for occupancy tracking and reporting.  
* **Base Rates (Example \- to be configured dynamically):**  
  * CAR\_SUV\_BASE\_MONTHLY\_ABONO: ARS 80,000  
  * TRUCK\_BASE\_MONTHLY\_ABONO: ARS 120,000  
  * MOTORCYCLE\_BASE\_MONTHLY\_ABONO: ARS 40,000  
  * **Inflation Adjustment for Abono:** While the *charge* for a single parking event is zero, the *base monthly fee* for the abono itself **should also be subject to the INFLATION\_ADJUSTMENT\_FACTOR** if new abonos are issued or existing ones are renewed. The admin UI should allow for **monthly adjustment of these base abono rates** as well. This is crucial for the owner's profitability.

#### **4.4. Nightly Rate (Estadía Nocturna) \- Optional/Configurable**

* **Description:** A reduced fixed rate for parking during specific overnight hours.  
* **Calculation Logic:** (As per previous version)  
* **Base Rates (Example \- to be configured dynamically):**  
  * CAR\_SUV\_BASE\_NIGHTLY\_RATE: ARS 25,000  
  * TRUCK\_BASE\_NIGHTLY\_RATE: ARS 35,000  
  * MOTORCYCLE\_BASE\_NIGHTLY\_RATE: ARS 12,500  
* **Effective Rate Calculation:** Effective\_Nightly\_Rate \= BASE\_NIGHTLY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

#### **4.5. Long-Term / Extended Stay Rates (for \> 1 day) \- Optional/Configurable**

* **Description:** Discounted daily rates for stays exceeding a certain number of days.  
* **Calculation Logic:** (As per previous version)  
* **Base Rates (Example \- to be configured dynamically):**  
  * CAR\_SUV\_BASE\_LONGTERM\_DAILY\_RATE: ARS 45,000  
  * TRUCK\_BASE\_LONGTERM\_DAILY\_RATE: ARS 65,000  
  * MOTORCYCLE\_BASE\_LONGTERM\_DAILY\_RATE: ARS 22,500  
* **Effective Rate Calculation:** Effective\_LongTerm\_Daily\_Rate \= BASE\_LONGTERM\_DAILY\_RATE \* INFLATION\_ADJUSTMENT\_FACTOR

### **5\. Charge Calculation Algorithm Flow (Cashier Module Logic)**

The cashier module should implement the following decision tree for calculating the charge:

1. **Receive Inputs:**  
   * Entry\_Timestamp  
   * Exit\_Timestamp  
   * Vehicle\_Type (CAR\_SUV, TRUCK, MOTORCYCLE)  
   * (Optional) Pre-selected\_Modality  
2. **Retrieve Current INFLATION\_ADJUSTMENT\_FACTOR:**  
   * Access the globally configured value from persistent storage.  
3. **Check for Monthly Abono:**  
   * Lookup Vehicle\_License\_Plate or Pass\_ID in the Monthly\_Abono\_Database.  
   * If found and valid for the current period, Total\_Charge \= 0\. Proceed to Payment Processing.  
   * If not found or invalid, proceed to next step.  
4. **Calculate Parking Duration:**  
   * Duration\_Minutes \= Exit\_Timestamp \- Entry\_Timestamp (in minutes).  
   * Convert to Duration\_Hours.  
5. **Evaluate Best Rate (Standard Logic \- unless specific modality chosen):**  
   * **Initialize Min\_Charge to infinity.**  
   * **A. Calculate Effective Hourly Rate Charge:**  
     * Effective\_Rate\_Per\_Hour \= BASE\_HOURLY\_RATE\[Vehicle\_Type\] \* INFLATION\_ADJUSTMENT\_FACTOR  
     * Full\_Hours \= floor(Duration\_Hours)  
     * Remaining\_Minutes \= Duration\_Minutes % 60  
     * Fractional\_Blocks \= ceil(Remaining\_Minutes / 30\) if Remaining\_Minutes \> 0, else 0\.  
     * Hourly\_Charge \= (Full\_Hours \+ Fractional\_Blocks if Full\_Hours \> 0 else ceil(Duration\_Hours)) \* Effective\_Rate\_Per\_Hour  
     * **Important:** If Duration\_Minutes \<= 60, Hourly\_Charge is simply Effective\_Rate\_Per\_Hour (first hour is full).  
     * Current\_Calculated\_Charge \= Hourly\_Charge  
     * **Check Daily Cap for Hourly:** If Current\_Calculated\_Charge exceeds Effective\_Daily\_Rate\[Vehicle\_Type\], then Current\_Calculated\_Charge \= Effective\_Daily\_Rate\[Vehicle\_Type\].  
     * If Current\_Calculated\_Charge \< Min\_Charge, then Min\_Charge \= Current\_Calculated\_Charge.  
   * **B. Calculate Effective Full Day Rate Charge:**  
     * Effective\_Rate\_Per\_Day \= BASE\_DAILY\_RATE\[Vehicle\_Type\] \* INFLATION\_ADJUSTMENT\_FACTOR  
     * Num\_Days \= ceil(Duration\_Hours / 24\)  
     * Daily\_Charge \= Num\_Days \* Effective\_Rate\_Per\_Day  
     * **Consider Partial Days:** If Duration\_Hours is between 6 and 24 hours, the Daily\_Charge should be applied directly.  
     * If Daily\_Charge \< Min\_Charge, then Min\_Charge \= Daily\_Charge.  
   * **C. Calculate Effective Nightly Rate (if enabled):**  
     * Effective\_Rate\_Per\_Night \= BASE\_NIGHTLY\_RATE\[Vehicle\_Type\] \* INFLATION\_ADJUSTMENT\_FACTOR  
     * Check if Entry\_Timestamp and Exit\_Timestamp fall within the defined nightly hours.  
     * If so, Nightly\_Charge \= Effective\_Rate\_Per\_Night.  
     * If Nightly\_Charge \< Min\_Charge, then Min\_Charge \= Nightly\_Charge.  
   * **D. Calculate Effective Long-Term Rate (if enabled):**  
     * Effective\_LongTerm\_Daily\_Rate \= BASE\_LONGTERM\_DAILY\_RATE\[Vehicle\_Type\] \* INFLATION\_ADJUSTMENT\_FACTOR  
     * If Duration\_Hours is \> (Long\_Term\_Threshold\_Days \* 24), then Long\_Term\_Daily\_Charge \= (Duration\_Hours / 24\) \* Effective\_LongTerm\_Daily\_Rate.  
     * If Long\_Term\_Daily\_Charge \< Min\_Charge, then Min\_Charge \= Long\_Term\_Daily\_Charge.  
6. **Final Charge:**  
   * Total\_Charge \= Min\_Charge (This is the optimal rate for the customer based on duration).  
7. **Display to Cashier:**  
   * Show Total\_Charge in ARS.  
   * Show Vehicle\_Type.  
   * Show Entry\_Timestamp, Exit\_Timestamp, Parking\_Duration.  
   * (Optional) Indicate which Payment\_Modality was automatically selected.

### **6\. Payment Processing Assistant**

Once the Total\_Charge is calculated:

* **Receive Payment Amount:** Cashier inputs the amount received from the customer.  
* **Calculate Change Due:** Change \= Payment\_Amount\_Received \- Total\_Charge.  
* **Display Change:** Show the change to be given to the customer.  
* **Record Transaction:** Log the transaction details (Entry/Exit, Vehicle Type, Modality Applied, Total Charge, Amount Received, Change, Timestamp of Transaction, Cashier ID).

### **7\. Configuration & Dynamic Rates (UI-Driven)**

* All **base rates** (hourly, daily, monthly, nightly, long-term, and their thresholds/fractions) **MUST be configurable** parameters within the system, accessible by an administrator via the **web interface (UI)**.  
* The system should support different rates for CAR\_SUV, TRUCK, and MOTORCYCLE for all relevant modalities.  
* The Monthly\_Abono\_Database must be managed separately (add, remove, update monthly pass holders).

#### **Key Inflation Adjustment Mechanism (Web UI Configuration)**

* **Dedicated UI Section:** There must be a clear and easily navigable section within the web interface, likely under an "Admin" or "Settings" menu, specifically for "Rate Adjustments" or "Inflation Factor."  
* **Input Field:** This section will feature a single, prominent input field where the administrator/owner can enter the INFLATION\_ADJUSTMENT\_FACTOR (e.g., a simple number input).  
  * **Label:** "Factor de Ajuste por Inflación (ej: 1.03 para 3%)" or similar clear language.  
  * **Current Value Display:** The current INFLATION\_ADJUSTMENT\_FACTOR should always be displayed prominently.  
* **Save Button:** A clear "Guardar" (Save) or "Aplicar Ajuste" (Apply Adjustment) button to commit the new value.  
* **Frequency:** The design should encourage this update typically **once per month** by the garage owner/manager.  
* **Confirmation:** A simple confirmation message should appear after successful update (e.g., "Factor de ajuste actualizado correctamente.").  
* **Storage:** The INFLATION\_ADJUSTMENT\_FACTOR must be persistently stored (e.g., in a configuration file or database) so it retains its value even after the system restarts.  
* **Validation:** The UI should implement client-side and server-side validation to ensure the input is a valid positive numeric value.  
* **Impact:** Upon updating this factor via the UI, all subsequent calculations for parking charges will use the new, adjusted rates immediately.

### **8\. System Status & Monitoring (for Cashier)**

* **Real-time Occupancy:** Display Total Slots, Occupied Slots, Available Slots (potentially with separate counts for TRUCK specific slots).  
* **Entry/Exit Log:** Show recent vehicle movements.  
* **Cashier Shift Summary:** At the end of a shift, allow the cashier to view a summary of transactions processed and total cash collected.  
* **Current Rate Display:** The cashier interface should display the *current effective rates* (e.g., "Tarifa Horaria: ARS 7,210") for clarity, reflecting the applied INFLATION\_ADJUSTMENT\_FACTOR. This helps the cashier explain charges to customers.