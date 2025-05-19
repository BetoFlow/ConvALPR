# ANPR Multi-Service Application Documentation

## 1. Introduction

This document describes the ANPR (Automatic Number Plate Recognition) Multi-Service Application. The system is designed for real-time license plate identification from various video sources, implements parking lot management logic (tracking vehicle entries and exits with a detailed session lifecycle), provides a financial module for charge calculation and rate management, and offers a web-based interface for visualization, configuration, and user management. It is deployed as a set of containerized microservices using Docker Compose.

## 2. System Architecture

The application utilizes a microservices architecture orchestrated by Docker Compose. It consists of five main services that communicate over a shared Docker network. Persistent data (MongoDB database files, detected plate images, uploaded videos) is managed using Docker volumes.

**Services Overview:**

*   **`anpr-service`**: The core engine responsible for video processing, license plate detection and recognition (using TensorFlow-based models), applying initial parking logic (entry/exit detection and basic state setting), saving detection images, and logging data to MongoDB. Fetches some operational parameters (like plate cooldown) from `cashier-service` at startup.
*   **`cashier-service`**: Manages financial configurations (inflation factor, base rates, grace period), provides an API for calculating parking charges, and records payment details. It also serves as a central store for some operational settings like timezone and plate cooldown.
*   **`mongodb`**: A NoSQL database used as the central data store for all application data, including raw detections, parking sessions, stream configurations, processing jobs, user accounts, rate configurations, and other settings.
*   **`mongo-express`**: A web-based administrative UI for MongoDB, allowing direct database inspection and management.
*   **`web-portal`**: A Flask-based web application providing user interaction features such as viewing detections and parking sessions, uploading videos for processing, managing persistent video stream configurations, user administration, and system settings configuration (interacting with `cashier-service` for financial/operational settings). It includes authentication and role-based access control.

**Conceptual Diagram:**

```mermaid
graph TD
    subgraph "User Interaction"
        UI[Web Browser]
    end

    subgraph "Application Services (Docker Network)"
        WP[web-portal <br> Flask App, Auth <br> Port: 5000]
        ANPR[anpr-service <br> Python, TF, OpenCV]
        CS[cashier-service <br> Python, Flask <br> Port: 5001]
        DB[(mongodb <br> Port: 27017)]
        ME[mongo-express <br> Port: 8081]
    end

    subgraph "Video Sources"
        RTSP[RTSP Streams]
        VID[Uploaded Videos]
        FILES[Local Video Files]
    end
    
    UI --- WP
    
    WP ---|HTTP API / DB Queries| DB
    WP ---|HTTP API (Settings, Charges, Payments)| CS
    
    ANPR ---|DB Writes/Reads (Sessions, Detections)| DB
    ANPR ---|HTTP API (Get Cooldown)| CS

    CS ---|DB Writes/Reads (Rates, Settings)| DB
    CS ---|DB Writes/Reads (Sessions for Payment)| DB
    
    ME ---|DB Admin| DB
    
    RTSP --> ANPR
    VID --> WP --> |Shared Volume| ANPR
    FILES --> ANPR

    style DB fill:#f9f,stroke:#333,stroke-width:2px
    style ANPR fill:#ccf,stroke:#333,stroke-width:2px
    style CS fill:#fca,stroke:#333,stroke-width:2px
    style WP fill:#cfc,stroke:#333,stroke-width:2px
    style ME fill:#ffc,stroke:#333,stroke-width:2px
```

## 3. Services Details

### 3.1. `anpr-service`
*   **Description**: This is the heart of the system for video processing and initial event detection. It ingests video, performs ANPR, implements initial parking session state logic, and logs relevant data.
*   **Key Technologies**: Python, OpenCV, TensorFlow, PyMongo, Requests.
*   **Core Logic**:
    *   **Video Ingestion**: Processes persistent video streams and on-demand uploaded files.
    *   **ANPR Processing**: Uses YOLO-based TensorFlow model for detection and a custom CNN model for OCR.
    *   **Parking Session Logic**:
        *   Creates new sessions with `session_state: VEHICLE_ENTERED` and default `vehicle_type: CAR_SUV`.
        *   Updates sessions on exit detection to `session_state: AWAITING_PAYMENT_RESOLUTION`.
    *   **Plate Detection Cooldown**: Fetches cooldown period from `cashier-service` at startup. Prevents re-processing the same plate by the same camera within this period.
    *   **Image Saving**: Saves detected plate images to a shared volume.
    *   **Data Logging**: Logs to `parking_sessions`, `detections` (optional raw logs), and `plate_last_seen_log`.
*   **Build & Location**: Source: `alpr/`, Dockerfile: `anpr/Dockerfile`.

### 3.2. `cashier-service` (New Service)
*   **Description**: Manages all financial configurations, calculates parking charges, records payments, and stores operational settings.
*   **Key Technologies**: Python, Flask, PyMongo.
*   **Port**: `5001` (internal).
*   **Core Logic**:
    *   **Settings Management API**: Provides endpoints for `web-portal` to get/set:
        *   Inflation Adjustment Factor.
        *   Base Parking Rates (per vehicle type/modality).
        *   Operational Timezone.
        *   Plate Detection Cooldown.
        *   Payment Grace Period.
    *   **Charge Calculation API**: Endpoint (`/api/calculate-charge`) that takes session details and returns calculated charge based on duration, vehicle type, rates, and inflation.
    *   **Payment Recording API**: Endpoint (`/api/record-payment`) that updates a parking session with payment details and transitions its `session_state` (e.g., to `PAID_AWAITING_EXIT` or `SESSION_CLOSED`).
*   **Build & Location**: Source: `cashier_service/`, Dockerfile: `cashier_service/Dockerfile`.

### 3.3. `mongodb`
*   **Description**: Primary data store.
*   **Key Technologies**: MongoDB.
*   **Port**: `27017`.
*   **Database Name**: `anpr_db` (default).
*   **Key Collections**: `detections`, `parking_sessions`, `stream_configs`, `video_jobs`, `plate_last_seen_log`, `users`, `rate_configs`, `inflation_factors` (used as a general settings store).
*   **Data Volume**: `anpr_db_data`.

### 3.4. `mongo-express`
*   **Description**: Web UI for MongoDB administration.
*   **Access**: `http://localhost:8081`. Credentials: `admin`/`password` (default).

### 3.5. `web-portal`
*   **Description**: A Flask web application providing the user interface with authentication and role-based access control. Interacts with `mongodb` directly for some data and with `cashier-service` for financial/operational settings and actions.
*   **Key Technologies**: Python, Flask, PyMongo, HTML/CSS, Flask-Login, Flask-Bcrypt, Requests, Pytz.
*   **Access**: `http://localhost:5000`
*   **Features**:
    *   **User Authentication & RBAC**: As previously described.
    *   **Parking Sessions Display**: Shows parking sessions. Timestamps displayed in configured operational timezone. Images are clickable to view full size.
    *   **Raw Detections Log**: Dedicated page (`/raw_detections`) displaying raw ANPR detections with search by plate. Timestamps localized. Images clickable.
    *   **Video Upload & Stream Management**: As previously described.
    *   **Session Editing**: As previously described. `session_state` is now displayed.
    *   **User Management**: As previously described.
    *   **Admin Settings**: UI to configure:
        *   Inflation Adjustment Factor.
        *   Base Parking Rates.
        *   Operational Timezone.
        *   Plate Detection Cooldown (notes `anpr-service` restart needed).
        *   Payment Grace Period.
    *   **Payment Processing**: UI for cashiers to process payments, fetches charges from `cashier-service`, calculates change, and records payment via `cashier-service` API.
    *   **Passive Timeout Check**: The main dashboard passively checks for sessions in `AWAITING_PAYMENT_RESOLUTION` that have exceeded the grace period and updates their state to `SESSION_UNPAID_TIMEOUT`.
*   **Build & Location**: Source: `web_portal/`, Dockerfile: `web_portal/Dockerfile`.

## 4. Data Flow Example (Parking Logic & Payment)

1.  **Vehicle Entry**:
    *   `anpr-service` detects a plate via an "entry" camera.
    *   `anpr-service` checks `plate_last_seen_log` against configured cooldown (fetched from `cashier-service` at startup).
    *   If new detection, `anpr-service` creates a `parking_sessions` record with `session_state: VEHICLE_ENTERED`, `vehicle_type: CAR_SUV` (default), entry details.
2.  **Vehicle Exit Detection**:
    *   `anpr-service` detects the same plate via an "exit" camera.
    *   `anpr-service` updates the session: records `exit_timestamp`, sets `session_state: AWAITING_PAYMENT_RESOLUTION`.
3.  **Payment (Scenario A: At Cashier Desk, before or at exit detection)**:
    *   Cashier in `web-portal` selects the session (state `VEHICLE_ENTERED` or `AWAITING_PAYMENT_RESOLUTION`).
    *   `web-portal` calls `cashier-service:/api/calculate-charge` to get the amount due.
    *   Cashier processes payment. `web-portal` calls `cashier-service:/api/record-payment`.
    *   `cashier-service` updates session:
        *   If original state was `VEHICLE_ENTERED`: `session_state: PAID_AWAITING_EXIT`, `payment_status: paid_pending_exit`.
        *   If original state was `AWAITING_PAYMENT_RESOLUTION`: `session_state: SESSION_CLOSED`, `status: exited`, `payment_status: completed_paid`.
4.  **Payment (Scenario B: Grace Period Timeout)**:
    *   Vehicle is in `AWAITING_PAYMENT_RESOLUTION`.
    *   `web-portal` (index route) passively checks if `exit_timestamp + grace_period` has passed.
    *   If timed out, `web-portal` updates session in DB: `session_state: SESSION_UNPAID_TIMEOUT`, `payment_status: unpaid_timeout`. (This session might later be manually transitioned to `SESSION_CLOSED`).
5.  **Session Closure**:
    *   Paid sessions become `SESSION_CLOSED` via `cashier-service` after payment.
    *   Unpaid timed-out sessions become `SESSION_UNPAID_TIMEOUT` and may require manual closure or further policy action.

## 5. Key Features Summary

*   Microservice architecture (`anpr-service`, `cashier-service`, `web-portal`, `mongodb`, `mongo-express`).
*   Real-time ANPR from multiple concurrent video streams.
*   TensorFlow-based models for plate detection and OCR.
*   **Advanced Parking Session Lifecycle**: Includes states like `VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`, `PAID_AWAITING_EXIT`, `SESSION_UNPAID_TIMEOUT`, `SESSION_CLOSED`.
*   **Configurable Financials & Operations (via Admin UI & `cashier-service`)**:
    *   Inflation Adjustment Factor.
    *   Base Parking Rates (per vehicle type/modality).
    *   Operational Timezone.
    *   Plate Detection Cooldown (requires `anpr-service` restart).
    *   Payment Grace Period.
*   **Default Vehicle Type**: New sessions default to `CAR_SUV`.
*   User Authentication and Role-Based Access Control.
*   Web portal for:
    *   Viewing parking sessions (timestamps localized, images clickable).
    *   Dedicated, searchable Raw Detections Log (timestamps localized, images clickable).
    *   Video upload, stream management, session editing, user management.
    *   Payment processing interface.
*   Visual database exploration via Mongo Express.

## 6. Deployment
*(Content remains the same)*

## 7. Configuration
*   **`docker-compose.yml`**: Defines services, networks, volumes, and some environment variables.
*   **`config.yaml` (for `anpr-service` at build time - less used now)**: Contains some ALPR model parameters. Many are now environment variables.
*   **Environment Variables**: Used extensively for runtime configuration of all services (MongoDB URI, Flask secret keys, model parameters, default settings).
    *   `CASHIER_SERVICE_URL`: While services can use Docker DNS (e.g., `http://cashier-service:5001`), this could be an override if needed (currently hardcoded to service name in `alpr/service_main.py` and `web_portal/app.py`).
*   **Admin Settings UI (via `web-portal` -> `cashier-service`)**: For dynamic configuration of rates and operational parameters.

## 8. Project Directory Structure Overview
*(Content remains the same)*

## 9. Minimum System Requirements (Estimates)
*(Content remains the same)*
