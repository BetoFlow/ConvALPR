# Explicación del Funcionamiento del Sistema Multi-Servicio ANPR

Este documento describe el flujo de trabajo y los componentes principales del sistema de reconocimiento automático de patentes vehiculares (ANPR) y gestión de estacionamiento implementado en este proyecto.

## 1. Introducción

El sistema ConvALPR ha evolucionado de un conjunto de scripts de ALPR a una **aplicación integral multi-servicio** diseñada para la identificación de patentes en tiempo real, la gestión completa del ciclo de vida de las sesiones de estacionamiento (entrada, salida, estados de pago), y la administración financiera de tarifas. La aplicación se despliega como un conjunto de microservicios containerizados utilizando **Docker Compose**, facilitando su instalación y manejo.

El núcleo del reconocimiento de patentes sigue utilizando **Redes Neuronales Convolucionales (CNNs)** para la localización y el Reconocimiento Óptico de Caracteres (OCR), permitiendo una alta precisión incluso en condiciones difíciles.

## 2. Arquitectura y Flujo General del Sistema

El sistema se compone de varios servicios que interactúan entre sí:

*   **`anpr-service`**: Procesa los flujos de video (RTSP, archivos locales, videos cargados). Realiza la detección de patentes y el OCR. Inicia y actualiza sesiones de estacionamiento en la base de datos con estados iniciales (`VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`) y asigna un tipo de vehículo por defecto (`CAR_SUV`). Guarda imágenes de las detecciones. Consulta la configuración de cooldown de detección (obtenida del `cashier-service`) al inicio.
*   **`cashier-service`**: Servicio central para la lógica de negocio financiera y configuraciones operativas.
    *   Gestiona y almacena configuraciones como: Factor de Inflación, Tarifas Base (por tipo de vehículo y modalidad), Zona Horaria Operacional, Cooldown de Detección de Patentes, y Período de Gracia para Pagos.
    *   Provee APIs para calcular los cargos de estacionamiento y para registrar los pagos, actualizando el estado de la sesión (ej. a `PAID_AWAITING_EXIT` o `SESSION_CLOSED`).
*   **`web-portal`**: Aplicación web Flask que sirve como interfaz de usuario.
    *   Permite a los administradores configurar todos los parámetros mencionados arriba (tarifas, inflación, zona horaria, cooldown, período de gracia) a través de una interfaz gráfica.
    *   Muestra listados de sesiones de estacionamiento y detecciones crudas (con timestamps ajustados a la zona horaria configurada e imágenes clickeables).
    *   Permite la carga de videos para procesamiento, gestión de cámaras/streams, y administración de usuarios y roles.
    *   Facilita el procesamiento de pagos interactuando con el `cashier-service`.
    *   Realiza una verificación pasiva de timeouts para sesiones en `AWAITING_PAYMENT_RESOLUTION`.
*   **`mongodb`**: Base de datos NoSQL donde se almacenan todos los datos persistentes: detecciones crudas, sesiones de estacionamiento, configuraciones de streams, trabajos de video, usuarios, configuraciones de tarifas y otros ajustes del sistema.
*   **`mongo-express`**: Herramienta web para la administración directa de la base de datos MongoDB.

**Flujo Simplificado de una Sesión de Estacionamiento:**
1.  Un vehículo entra. `anpr-service` lo detecta, crea una sesión con estado `VEHICLE_ENTERED` y `vehicle_type: CAR_SUV`.
2.  El vehículo es detectado en una cámara de salida. `anpr-service` actualiza la sesión a `AWAITING_PAYMENT_RESOLUTION` y registra el `exit_timestamp`.
3.  **Pago**:
    *   Si se paga antes del timeout (a través del `web-portal` que llama al `cashier-service`): `cashier-service` actualiza la sesión a `SESSION_CLOSED` (estado final, pagado). Si se pagó antes de la detección de salida, el estado intermedio es `PAID_AWAITING_EXIT`.
    *   **Timeout**: Si el período de gracia (configurable) transcurre después de `AWAITING_PAYMENT_RESOLUTION` sin pago, el `web-portal` (al mostrar los datos) puede actualizar la sesión a `SESSION_UNPAID_TIMEOUT`.
4.  La sesión finaliza en `SESSION_CLOSED` (pagada o no pagada tras timeout).

## 3. Componente Principal de ANPR (`alpr/alpr.py` dentro de `anpr-service`)

La clase `ALPR` encapsula la lógica de detección y reconocimiento.

*   **Inicialización (`__init__`):**
    *   Recibe la URI de MongoDB, una instancia de conexión a la BD (`db_instance` para acceder a `parking_sessions` y `plate_last_seen_log`), y configuraciones como `log_raw_detections` y `plate_cooldown_seconds` (obtenido del `cashier-service` por `service_main.py`).
    *   Carga los modelos de **detección** (`PlateDetector`) y **OCR** (`PlateOCR`) con sus respectivos parámetros de configuración (resolución, umbrales de confianza, número de modelo OCR).
    *   Inicializa las colecciones de MongoDB necesarias: `parking_sessions_collection` y `plate_last_seen_log_collection`.

*   **Método `process_frame`:**
    *   Orquesta el proceso de ANPR para cada frame:
        1.  Detección de patentes usando `PlateDetector`.
        2.  OCR sobre cada patente detectada usando `PlateOCR`.
        3.  **Lógica de Cooldown**: Verifica en `plate_last_seen_log_collection` si la patente+cámara fue vista recientemente para evitar detecciones duplicadas dentro del período de cooldown configurado.
        4.  **Guardado de Imagen**: Guarda la imagen de la detección si se cumplen los criterios.
        5.  **Lógica de Sesiones de Estacionamiento**:
            *   Busca una sesión activa para la patente detectada.
            *   Si no existe y la cámara es de entrada/común: crea una nueva sesión en `parking_sessions_collection` con `session_state: "VEHICLE_ENTERED"`, `vehicle_type: "CAR_SUV"`, y otros detalles de entrada.
            *   Si existe una sesión activa y la cámara es de salida/común: actualiza la sesión existente, registrando el `exit_timestamp` y cambiando `session_state` a `"AWAITING_PAYMENT_RESOLUTION"`.
            *   Actualiza `last_seen_timestamp` para sesiones activas.
        6.  Actualiza el registro en `plate_last_seen_log_collection`.
        7.  Si `log_raw_detections` es `True`, llama a `super().add_detection_record` para guardar la detección cruda (usando la lógica de `MongoSaver`).
    *   Devuelve información sobre las patentes procesadas.

## 4. Detección de Patentes (`alpr/detector.py`)

(La descripción de esta clase y su funcionamiento interno para localizar patentes mediante YOLOv4-tiny sigue siendo mayormente relevante como se describió originalmente.)

## 5. Reconocimiento de Caracteres (OCR) (`alpr/ocr.py`)

(La descripción de esta clase y su funcionamiento interno para reconocer caracteres mediante CNNs personalizadas sigue siendo mayormente relevante como se describió originalmente.)

## 6. Guardado de Resultados

El sistema ahora utiliza **MongoDB** como base de datos central:

*   **Sesiones de Estacionamiento (`parking_sessions` collection)**: Gestionadas directamente por la lógica en `ALPR.process_frame` (en `alpr/alpr.py`) y actualizadas por `cashier-service` durante el proceso de pago. Contienen el ciclo de vida completo de la estadía del vehículo.
*   **Detecciones Crudas (`detections` collection)**: El guardado de estas detecciones (si está habilitado por `log_raw_detections`) es manejado por la clase `MongoSaver` (en `alpr/saver.py`), de la cual `ALPR` hereda. `MongoSaver` acumula detecciones en un batch y las inserta periódicamente.
*   **Configuraciones y Otros Datos**: Colecciones como `users`, `stream_configs`, `rate_configs`, `inflation_factors` (que también almacena timezone, cooldown, grace period) son gestionadas por `web-portal` y/o `cashier-service`.

## 7. Configuración del Sistema

La configuración se maneja de varias formas:

*   **`docker-compose.yml`**: Define los servicios, redes, volúmenes y, crucialmente, las **variables de entorno** para cada servicio. Estas variables son la forma principal de configurar parámetros en tiempo de ejecución (ej. URI de MongoDB, claves secretas de Flask, algunos parámetros de los modelos ALPR).
*   **Interfaz de Administrador en `web-portal`**: Permite la configuración dinámica de:
    *   Factor de Ajuste por Inflación.
    *   Tarifas Base de estacionamiento.
    *   Zona Horaria Operacional.
    *   Cooldown de Detección de Patentes.
    *   Período de Gracia para Pagos.
    Estos ajustes se almacenan en MongoDB a través del `cashier-service`.
*   **`config.yaml`**: Puede seguir siendo utilizado por `anpr-service` para cargar configuraciones estáticas de los modelos de ALPR si no se sobrescriben por variables de entorno. Su relevancia ha disminuido en favor de la configuración dinámica y por entorno.

## 8. Conclusión

El sistema ConvALPR es ahora una aplicación robusta y modular basada en microservicios, que no solo realiza el reconocimiento de patentes con alta precisión, sino que también ofrece una gestión completa de estacionamientos, incluyendo un ciclo de vida detallado de sesiones, manejo financiero configurable, y una interfaz web para operación y administración. El uso de Docker y Docker Compose simplifica enormemente su despliegue y mantenimiento.
