# Explicación del Funcionamiento del Sistema Multi-Servicio ANPR

Este documento describe el flujo de trabajo y los componentes principales del sistema de reconocimiento automático de patentes vehiculares (ANPR) y gestión de estacionamiento implementado en este proyecto.

## 1. Introducción

El sistema ConvALPR ha evolucionado de un conjunto de scripts de ALPR a una **aplicación integral multi-servicio** diseñada para la identificación de patentes en tiempo real, la gestión completa del ciclo de vida de las sesiones de estacionamiento (entrada, salida, estados de pago), y la administración financiera de tarifas. La aplicación se despliega como un conjunto de microservicios containerizados utilizando **Docker Compose**, facilitando su instalación y manejo.

El núcleo del reconocimiento de patentes sigue utilizando **Redes Neuronales Convolucionales (CNNs)** para la localización y el Reconocimiento Óptico de Caracteres (OCR), permitiendo una alta precisión incluso en condiciones difíciles. La gestión del ciclo de vida de las sesiones de estacionamiento ahora está centralizada mediante una **máquina de estados formal (`ParkingSessionStateMachine`) ubicada en un módulo compartido `common`**.

## 2. Arquitectura y Flujo General del Sistema

El sistema se compone de varios servicios que interactúan entre sí y con el módulo `common`:

*   **Módulo `common`**: Un módulo Python compartido que contiene `ParkingSessionStateMachine.py`. Esta máquina de estados es la autoridad central para los estados (`INIT`, `VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`, `PAID_AWAITING_EXIT`, `SESSION_UNPAID_TIMEOUT`, `SESSION_CLOSED`) y las transiciones de las sesiones de estacionamiento. Es importado y utilizado por los servicios `anpr-service`, `cashier-service` y `web-portal`.
*   **`anpr-service`**: Procesa los flujos de video. Realiza la detección de patentes y el OCR. Al detectar entradas/salidas de vehículos, **desencadena eventos (`event_detect_entry`, `event_detect_exit`) en la `ParkingSessionStateMachine`**. La FSM gestiona los cambios de estado y prepara los datos para MongoDB.
*   **`cashier-service`**: Servicio central para la lógica de negocio financiera y configuraciones operativas. Para el registro de pagos, **desencadena un evento `payment_received` en la `ParkingSessionStateMachine`**. La FSM actualiza el estado de la sesión y los detalles del pago.
*   **`web-portal`**: Aplicación web Flask que sirve como interfaz de usuario. Para los timeouts de sesión, **desencadena un evento `event_payment_timeout` en la `ParkingSessionStateMachine`**. Permite la configuración de parámetros del sistema.
*   **`mongodb`**: Base de datos NoSQL donde se almacenan todos los datos persistentes.
*   **`mongo-express`**: Herramienta web para la administración directa de la base de datos MongoDB.

**Flujo Simplificado de una Sesión de Estacionamiento (con FSM):**
1.  Un vehículo entra. `anpr-service` lo detecta y prepara los datos de entrada.
    *   Instancia `ParkingSessionStateMachine` (estado inicial `INIT`) para un nuevo ID de sesión.
    *   Desencadena `event_detect_entry` en la FSM. La FSM transiciona a `VEHICLE_ENTERED` y prepara los datos iniciales de la sesión.
    *   `anpr-service` inserta el documento inicial de la sesión en MongoDB.
2.  El vehículo es detectado en una cámara de salida. `anpr-service` carga la FSM de la sesión existente.
    *   Desencadena `event_detect_exit` en la FSM con los detalles de salida.
    *   La FSM transiciona el estado (ej. a `AWAITING_PAYMENT_RESOLUTION` si venía de `VEHICLE_ENTERED`, o a `SESSION_CLOSED` si venía de `PAID_AWAITING_EXIT`) y actualiza el documento en MongoDB.
3.  **Pago**:
    *   El usuario realiza el pago a través del `web-portal`, que llama a la API `/api/record-payment` del `cashier-service`.
    *   `cashier-service` carga la FSM de la sesión.
    *   Verifica si el evento `payment_received` es válido y lo desencadena con los detalles del pago.
    *   La FSM transiciona el estado (ej. a `PAID_AWAITING_EXIT` o `SESSION_CLOSED`) y actualiza el documento en MongoDB.
4.  **Timeout**: Si el período de gracia transcurre para una sesión en `AWAITING_PAYMENT_RESOLUTION`, el `web-portal` carga la FSM y desencadena `event_payment_timeout`. La FSM transiciona a `SESSION_UNPAID_TIMEOUT` y actualiza MongoDB.
5.  La sesión finaliza en `SESSION_CLOSED` (pagada o no pagada tras timeout), según las transiciones definidas en la FSM.

## 3. Componente Principal de ANPR (`alpr/alpr.py` dentro de `anpr-service`)

La clase `ALPR` encapsula la lógica de detección y reconocimiento.

*   **Inicialización (`__init__`):**
    *   (Similar a la descripción original, pero ahora también importa `ParkingSessionStateMachine` y `ObjectId` de `bson`).
    *   Recibe `db_instance` para que la FSM pueda interactuar con `parking_sessions_collection`.

*   **Método `process_frame`:**
    *   Orquesta el proceso de ANPR:
        1.  Detección y OCR (como antes).
        2.  Lógica de Cooldown (como antes).
        3.  Guardado de Imagen (como antes).
        4.  **Lógica de Sesiones de Estacionamiento (ahora con FSM)**:
            *   Busca una sesión activa.
            *   Si no existe y la cámara es de entrada/común:
                *   Crea un `ObjectId` para la nueva sesión.
                *   Instancia `ParkingSessionStateMachine` con estado `INIT`.
                *   Desencadena `event_detect_entry` con los datos de entrada. La FSM actualiza sus datos internos y transiciona a `VEHICLE_ENTERED`.
                *   `anpr-service` inserta el documento inicial en `parking_sessions` usando los datos preparados por la FSM.
            *   Si existe una sesión activa y la cámara es de salida/común:
                *   Carga la FSM de la sesión existente usando `ParkingSessionStateMachine.load_session()`.
                *   Desencadena `event_detect_exit` con los datos de salida. La FSM maneja la transición de estado y la actualización del documento en MongoDB a través de su callback `_persist_state`.
            *   Actualiza el registro en `plate_last_seen_log_collection`.
        5.  Si `log_raw_detections` es `True`, guarda la detección cruda.
    *   Devuelve información sobre las patentes procesadas.

## 4. Detección de Patentes (`alpr/detector.py`)
(La descripción de esta clase y su funcionamiento interno para localizar patentes mediante YOLOv4-tiny sigue siendo mayormente relevante como se describió originalmente.)

## 5. Reconocimiento de Caracteres (OCR) (`alpr/ocr.py`)
(La descripción de esta clase y su funcionamiento interno para reconocer caracteres mediante CNNs personalizadas sigue siendo mayormente relevante como se describió originalmente.)

## 6. Guardado de Resultados

El sistema utiliza **MongoDB** como base de datos central:

*   **Sesiones de Estacionamiento (`parking_sessions` collection)**:
    *   La creación inicial del documento es realizada por `anpr-service` después de que la FSM procesa un `event_detect_entry`.
    *   Las actualizaciones subsecuentes de estado y datos (ej. detalles de salida, información de pago) son manejadas por la `ParkingSessionStateMachine` a través de su callback `_persist_state`, que es invocado después de cada transición de estado exitosa.
*   **Detecciones Crudas (`detections` collection)**: (Sin cambios, manejado por `MongoSaver`).
*   **Configuraciones y Otros Datos**: (Sin cambios).

## 7. Configuración del Sistema

La configuración se maneja de varias formas:

*   **`docker-compose.yml`**: Define los servicios, redes, volúmenes, **contextos de build (ahora estandarizados a la raíz del proyecto)**, y variables de entorno.
*   **Dockerfiles (`anpr/`, `cashier_service/`, `web_portal/`)**: Actualizados para copiar el módulo `common` desde la raíz del proyecto y para establecer `ENV PYTHONPATH /app`, asegurando que los módulos compartidos sean importables.
*   **Interfaz de Administrador en `web-portal`**: (Sin cambios funcionales directos por la FSM, pero los servicios que consumen estas configuraciones ahora pueden interactuar con la FSM).
*   **`config.yaml`**: (Sin cambios).

## 8. Conclusión

El sistema ConvALPR es ahora una aplicación robusta y modular basada en microservicios. La introducción de la `ParkingSessionStateMachine` centraliza la lógica del ciclo de vida de las sesiones, mejorando la mantenibilidad y claridad del flujo de estados. El sistema continúa ofreciendo reconocimiento de patentes de alta precisión y una gestión completa de estacionamientos, con despliegue simplificado mediante Docker.
