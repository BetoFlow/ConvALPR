# ANPR Multi-Service Application Documentation

## 1. Introduction

This document describes the ANPR (Automatic Number Plate Recognition) Multi-Service Application. The system is designed for real-time license plate identification from various video sources. It implements parking lot management logic, featuring a detailed session lifecycle managed by a **centralized state machine (`ParkingSessionStateMachine` located in a shared `common` module)**. The system also provides a financial module for charge calculation and rate management, and offers a web-based interface for visualization, configuration, and user management. It is deployed as a set of containerized microservices using Docker Compose.

## 2. System Architecture

The application utilizes a microservices architecture orchestrated by Docker Compose. It consists of several main services that communicate over a shared Docker network and interact with a shared `common` module for state management. Persistent data is managed using Docker volumes.

**Services Overview:**

*   **`common` module (New)**: A shared Python module containing `ParkingSessionStateMachine.py`. This state machine is the central authority for parking session states and transitions. Services trigger events on FSM instances to manage session lifecycles.
*   **`anpr-service`**: The core engine for video processing and ANPR. It detects vehicle entries and exits, then **triggers corresponding events (`event_detect_entry`, `event_detect_exit`) on the `ParkingSessionStateMachine`**. The FSM handles state changes and prepares data for MongoDB. Fetches operational parameters like plate cooldown.
*   **`cashier-service`**: Manages financial configurations and charge calculations. For payment recording, it **triggers a `payment_received` event on the `ParkingSessionStateMachine`**. The FSM updates the session state and payment details.
*   **`mongodb`**: A NoSQL database serving as the central data store.
*   **`mongo-express`**: A web-based administrative UI for MongoDB.
*   **`web-portal`**: Flask web application for UI. For session timeouts, it **triggers an `event_payment_timeout` on the `ParkingSessionStateMachine`**.

**Conceptual Diagram:**

```mermaid
graph TD
    subgraph "User Interaction"
        UI[Web Browser]
    end

    subgraph "Application Logic"
        FSM[common/ParkingSessionStateMachine]
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
    WP ---|HTTP API (Settings, Charges)| CS
    WP ---|Triggers FSM Events (Timeout)| FSM

    ANPR ---|Triggers FSM Events (Entry/Exit)| FSM
    ANPR ---|HTTP API (Get Cooldown)| CS

    CS ---|Triggers FSM Events (Payment)| FSM
    CS ---|DB Writes/Reads (Rates, Settings for Calc)| DB
    
    FSM ---|Updates/Reads Session State| DB[parking_sessions]

    ME ---|DB Admin| DB
    
    RTSP --> ANPR
    VID --> WP --> |Shared Volume| ANPR
    FILES --> ANPR

    style DB fill:#f9f,stroke:#333,stroke-width:2px
    style ANPR fill:#ccf,stroke:#333,stroke-width:2px
    style CS fill:#fca,stroke:#333,stroke-width:2px
    style WP fill:#cfc,stroke:#333,stroke-width:2px
    style ME fill:#ffc,stroke:#333,stroke-width:2px
    style FSM fill:#e6e6fa,stroke:#333,stroke-width:2px
```

## 3. Services Details

### 3.0. `common` Module (Shared)
*   **Description**: Contains shared code, primarily the `ParkingSessionStateMachine`.
*   **Key Component**: `ParkingSessionStateMachine.py` defines the states (`INIT`, `VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`, `PAID_AWAITING_EXIT`, `SESSION_UNPAID_TIMEOUT`, `SESSION_CLOSED`) and transitions for parking sessions. It uses the `transitions` library and includes logic to persist state changes to the `parking_sessions` collection in MongoDB.
*   **Usage**: Imported by `anpr-service`, `cashier-service`, and `web-portal` to manage session state transitions by triggering events.
*   **Location**: `common/` directory at the project root. Copied into each service's Docker image.

### 3.1. `anpr-service`
*   **Description**: Video processing, ANPR, and initial parking event detection.
*   **Key Technologies**: Python, OpenCV, TensorFlow, PyMongo, Requests, `transitions` (via `common` module).
*   **Core Logic**:
    *   **Parking Session Logic (Interaction with FSM)**:
        *   On new vehicle entry: Creates a new session ID, instantiates `ParkingSessionStateMachine` (initial state `INIT`), triggers `event_detect_entry` with plate and entry details. The FSM transitions to `VEHICLE_ENTERED`. The `anpr-service` then inserts the initial session document into MongoDB using data prepared by the FSM.
        *   On vehicle exit detection: Loads the existing session's FSM, triggers `event_detect_exit`. The FSM handles the transition (e.g., to `AWAITING_PAYMENT_RESOLUTION` or `SESSION_CLOSED` if pre-paid) and updates the session document in MongoDB.
*   **(Other details like Video Ingestion, ANPR Processing, Cooldown, Image Saving remain largely the same but now feed into FSM event triggers.)**
*   **Build & Location**: Source: `alpr/`, Dockerfile: `anpr/Dockerfile`.

### 3.2. `cashier-service`
*   **Description**: Manages financial configurations, calculates charges, and processes payments by interacting with the FSM.
*   **Key Technologies**: Python, Flask, PyMongo, `transitions` (via `common` module).
*   **Core Logic**:
    *   **Payment Recording API (`/api/record-payment`)**:
        *   Loads the `ParkingSessionStateMachine` for the specified session.
        *   Verifies if the `payment_received` event is valid for the current state.
        *   Triggers the `payment_received` event on the FSM, passing payment details.
        *   The FSM updates the session state (e.g., to `PAID_AWAITING_EXIT` or `SESSION_CLOSED`) and persists payment information to MongoDB.
*   **(Settings Management and Charge Calculation APIs remain functionally similar but are now decoupled from direct state manipulation.)**
*   **Build & Location**: Source: `cashier_service/`, Dockerfile: `cashier_service/Dockerfile`.

### 3.3. `mongodb`
(Content remains the same)

### 3.4. `mongo-express`
(Content remains the same)

### 3.5. `web-portal`
*   **Description**: Flask web UI. Interacts with `cashier-service` and triggers FSM events for timeouts.
*   **Key Technologies**: Python, Flask, PyMongo, HTML/CSS, Flask-Login, Flask-Bcrypt, Requests, Pytz, `transitions` (via `common` module).
*   **Core Logic**:
    *   **Passive Timeout Check (in `index` route)**:
        *   For sessions in `AWAITING_PAYMENT_RESOLUTION`, if grace period expires:
        *   Loads the `ParkingSessionStateMachine` for the session.
        *   Triggers the `event_payment_timeout`. The FSM transitions the state to `SESSION_UNPAID_TIMEOUT` and updates MongoDB.
*   **(Other features like User Auth, Display, Admin Settings, Payment UI remain functionally similar but rely on the FSM-managed states.)**
*   **Build & Location**: Source: `web_portal/`, Dockerfile: `web_portal/Dockerfile`.

## 4. Data Flow Example (Parking Logic & Payment with FSM)

1.  **Vehicle Entry**:
    *   `anpr-service` detects a plate. After cooldown check, it prepares entry data.
    *   It instantiates `ParkingSessionStateMachine` (initial state `INIT`) for a new session ID.
    *   Triggers `event_detect_entry` on the FSM with entry details.
    *   FSM transitions to `VEHICLE_ENTERED`, its `_prepare_entry_data` callback populates internal session data.
    *   `anpr-service` inserts the initial session document (including `_id`, `session_state: VEHICLE_ENTERED`, and other FSM-prepared data) into `parking_sessions`.
2.  **Vehicle Exit Detection**:
    *   `anpr-service` detects the same plate at an exit.
    *   It loads the FSM for the existing session using `ParkingSessionStateMachine.load_session()`.
    *   Triggers `event_detect_exit` on the FSM with exit details.
    *   FSM transitions:
        *   If current state was `VEHICLE_ENTERED`, new state is `AWAITING_PAYMENT_RESOLUTION`.
        *   If current state was `PAID_AWAITING_EXIT`, new state is `SESSION_CLOSED`.
    *   FSM's `_persist_state` callback updates the session document in MongoDB.
3.  **Payment**:
    *   User initiates payment via `web-portal`. `web-portal` calls `cashier-service:/api/record-payment`.
    *   `cashier-service` loads the FSM for the session.
    *   It checks if `payment_received` is a valid trigger from the current state (e.g., `VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`).
    *   Triggers `payment_received` event on FSM with payment details.
    *   FSM transitions (e.g., to `PAID_AWAITING_EXIT` or `SESSION_CLOSED`) and `_persist_state` updates MongoDB.
4.  **Grace Period Timeout**:
    *   `web-portal` (index route) identifies a session in `AWAITING_PAYMENT_RESOLUTION` whose grace period has expired.
    *   Loads the FSM for that session.
    *   Checks if `event_payment_timeout` is a valid trigger.
    *   Triggers `event_payment_timeout`. FSM transitions to `SESSION_UNPAID_TIMEOUT` and `_persist_state` updates MongoDB.
5.  **Session Closure**:
    *   Managed by FSM transitions to `SESSION_CLOSED` based on various events (payment after exit, exit after pre-payment, forced closure of unpaid timeout).

## 5. Key Features Summary

*   Microservice architecture with a **shared `common` module for state management**.
*   **Centralized State Management**: Parking session lifecycle governed by `ParkingSessionStateMachine`.
*   Real-time ANPR from multiple concurrent video streams.
*   TensorFlow-based models for plate detection and OCR.
*   Configurable Financials & Operations (via Admin UI & `cashier-service`).
*   User Authentication and Role-Based Access Control.
*   Web portal for visualization, configuration, and operations.
*   Visual database exploration via Mongo Express.

## 6. Deployment
*(Content remains the same)*

## 7. Configuration
*   **`docker-compose.yml`**: Defines services, networks, volumes, build contexts (now standardized to project root), and Dockerfile paths.
*   **Dockerfiles (`anpr/`, `cashier_service/`, `web_portal/`)**: Updated to copy the `common` module and set `ENV PYTHONPATH /app`.
*   **(Other configuration details remain similar.)**

## 8. Project Directory Structure Overview
*   `alpr/`: ALPR service code and models.
*   `cashier_service/`: Cashier service Flask application.
*   `common/`: Shared modules, including `session_state_machine.py`.
*   `web_portal/`: Web portal Flask application, templates, static files.
*   `assets/`: Sample video files, etc.
*   `config.yaml`: Static configuration (less used now for dynamic settings).
*   `docker-compose.yml`: Docker orchestration file.
*   `*.md`: Markdown documentation files.
*   `requirements.txt` files within each service directory.

## 9. Minimum System Requirements (Estimates)
*(Content remains the same)*
