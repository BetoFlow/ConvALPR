# ConvALPR: Sistema Multi-Servicio de Reconocimiento Automático de Patentes y Gestión de Estacionamiento

[![Alt Text](assets/alpr.gif)](https://youtu.be/-TPJot7-HTs?t=652)

**ConvALPR** es un sistema avanzado de Reconocimiento Automático de Patentes Vehiculares (ANPR) que utiliza **Redes Neuronales Convolucionales (CNNs)**. A diferencia de métodos tradicionales, este enfoque permite reconocer patentes incluso con obstrucciones, diferencias de brillo, o caracteres borrosos.

Originalmente enfocado en los procesos de **localización** (detector de objetos) y **reconocimiento** (OCR) de patentes, ConvALPR ha evolucionado a un **sistema integral multi-servicio** para la gestión de estacionamientos. Este sistema incluye un portal web para visualización y administración, un módulo de caja para la gestión financiera, y se despliega fácilmente mediante **Docker Compose**.

![Proceso ALPR](assets/proceso.png)

## Arquitectura del Sistema

La aplicación utiliza una arquitectura de microservicios orquestada por Docker Compose. Consiste en los siguientes servicios principales:

*   **`anpr-service`**: Motor central para el procesamiento de video, detección y reconocimiento de patentes (TensorFlow), lógica inicial de sesiones de estacionamiento, y guardado de datos en MongoDB.
*   **`cashier-service`**: Gestiona configuraciones financieras (tarifas, inflación, período de gracia), calcula cargos de estacionamiento, y registra pagos. También almacena configuraciones operativas (zona horaria, cooldown de detección).
*   **`web-portal`**: Aplicación web Flask que provee la interfaz de usuario para visualización de sesiones y detecciones, carga de videos, gestión de cámaras, administración de usuarios y configuración del sistema.
*   **`mongodb`**: Base de datos NoSQL central para todos los datos de la aplicación.
*   **`mongo-express`**: Interfaz web administrativa para MongoDB.

## Características Principales

*   **Despliegue Sencillo con Docker Compose**: Todo el sistema se levanta con un solo comando.
*   **ANPR en Tiempo Real**: Procesamiento de múltiples fuentes de video concurrentes.
*   **Modelos Avanzados**: Utiliza modelos basados en TensorFlow (YOLOv4-tiny para detección, CNNs personalizadas para OCR).
*   **Gestión Integral de Estacionamiento**:
    *   Seguimiento de entrada/salida de vehículos.
    *   Ciclo de vida detallado de sesiones de estacionamiento (`VEHICLE_ENTERED`, `AWAITING_PAYMENT_RESOLUTION`, `PAID_AWAITING_EXIT`, `SESSION_UNPAID_TIMEOUT`, `SESSION_CLOSED`).
    *   Tipo de vehículo por defecto: `CAR_SUV` para nuevas sesiones.
*   **Portal Web Interactivo (`web-portal`)**:
    *   Visualización de sesiones de estacionamiento y detecciones crudas (con timestamps localizados).
    *   Imágenes de detección clickeables para vista en tamaño completo.
    *   Página dedicada para el registro de detecciones crudas con funcionalidad de búsqueda por patente.
    *   Carga de videos para procesamiento bajo demanda.
    *   Gestión de cámaras y fuentes de video persistentes.
    *   Autenticación de usuarios y roles (admin, supervisor, técnico, operador).
    *   Edición de sesiones y gestión de usuarios según roles.
*   **Módulo de Caja y Configuración Financiera (`cashier-service` y Admin UI)**:
    *   Configuración de Factor de Ajuste por Inflación.
    *   Configuración de Tarifas Base (por tipo de vehículo y modalidad: horaria, diaria, nocturna, abonos).
    *   Cálculo de cargos de estacionamiento.
    *   Procesamiento de pagos (simulado o integrado).
*   **Configuraciones Operativas (Admin UI)**:
    *   Zona Horaria Operacional.
    *   Cooldown para Detección de Patentes (requiere reinicio de `anpr-service`).
    *   Período de Gracia para Pagos.
*   **Base de Datos Centralizada**: MongoDB para persistencia de datos, accesible vía Mongo Express.

## Cómo Usarlo (Aplicación Multi-Servicio con Docker)

### Requisitos Previos

*   **Docker**: [Instrucciones de instalación](https://docs.docker.com/get-docker/)
*   **Docker Compose**: Generalmente se instala con Docker. [Instrucciones](https://docs.docker.com/compose/install/)

### Instalación y Ejecución

1.  **Clonar el Repositorio (si aún no lo has hecho):**
    ```bash
    git clone <URL_DEL_REPOSITORIO>
    cd ConvALPR 
    ```
2.  **Construir e Iniciar los Servicios con Docker Compose:**
    Desde la raíz del proyecto (`ConvALPR/`), ejecuta:
    ```bash
    docker compose up --build -d
    ```
    Este comando construirá las imágenes de los servicios (si es la primera vez o si hay cambios en los Dockerfiles) y luego iniciará todos los contenedores en segundo plano (`-d`).

### Acceso a los Servicios

*   **Portal Web Principal (`web-portal`)**:
    *   URL: `http://localhost:5000`
    *   Credenciales de administrador por defecto: `admin` / `admin` (se recomienda cambiarla).
*   **Mongo Express (Administración de Base de Datos)**:
    *   URL: `http://localhost:8081`

### Configuración del Sistema

La configuración principal del sistema se realiza a través de:

1.  **Variables de Entorno**: Definidas en el archivo `docker-compose.yml` para cada servicio. Estas controlan aspectos como la URI de MongoDB, claves secretas, y parámetros de los modelos de ANPR.
2.  **Interfaz de Configuración de Administrador (en el Portal Web)**:
    *   Una vez logueado como administrador en `http://localhost:5000`, navega a "Admin Settings".
    *   Desde aquí puedes configurar:
        *   Factor de Ajuste por Inflación.
        *   Tarifas Base para diferentes tipos de vehículos y modalidades.
        *   Zona Horaria Operacional del sistema.
        *   Tiempo de Cooldown para la detección de patentes (requiere reinicio del `anpr-service`).
        *   Período de Gracia para pagos.
3.  **`config.yaml`**: Este archivo (ubicado en la raíz) aún puede ser utilizado por el `anpr-service` para parámetros específicos de los modelos de detección y OCR que no se configuran por variables de entorno.

---

## Componentes Individuales de ALPR (Para Desarrollo y Pruebas)

Las siguientes secciones describen cómo probar los componentes de localización y OCR de forma aislada, utilizando scripts de Python. Esto es útil para desarrollo o pruebas específicas de los modelos de ALPR, pero **no es la forma de ejecutar la aplicación completa**.

### Instalar Dependencias (para scripts individuales)

Se recomienda utilizar un entorno virtual.

1.  **Crear un entorno virtual:** `python3 -m venv .venv`
2.  **Activar el entorno virtual:**
    *   Linux/macOS: `source .venv/bin/activate`
    *   Windows: `.\.venv\Scripts\activate`
3.  **Instalar las dependencias:** `pip install -r requirements.txt`
    (Para GPU, asegúrate de tener los [requisitos de TensorFlow para GPU](https://www.tensorflow.org/install/gpu#software_requirements) antes).

### Localizador (Detector de Patentes)

![Demo yolo v4 tiny](assets/demo_localizador.gif)

Utiliza YOLOv4-tiny. Modelos en [`alpr/models/detection`](alpr/models/detection/) con resoluciones de entrada de {*384x384*, *512x512*, *608x608*}.

**Para probar solo el localizador (sin OCR):**
```bash
python detector_demo.py --fuente-video /path/a/tu/video.mp4 --mostrar-resultados --input-size 608
```

### Reconocedor (OCR)

![Demo OCR](https://github.com/ankandrew/cnn-ocr-lp/blob/master/extra/demo.gif)

Modelos personalizados en TensorFlow Keras, ubicados en [`alpr/models/ocr`](alpr/models/ocr/).

### Scripts de Prueba del ALPR Completo (Localizador + OCR)

*   **Ejemplo para visualizar predicciones ALPR (sin guardar en DB):**
    ```bash
    python reconocedor_automatico.py --cfg config.yaml --demo
    ```
*   **Ejemplo para procesar y guardar en DB (sin visualizar):**
    ```bash
    python reconocedor_automatico.py --cfg config.yaml
    ```
    *(Nota: Este script guarda en una base de datos SQLite local, no en el MongoDB usado por la aplicación Dockerizada).*

---

## Notas Adicionales

*   **Reconocedor OCR para Argentina**: Si bien el localizador puede funcionar con patentes de diversos países, el modelo OCR actual está entrenado principalmente para patentes de Argentina. Para otros formatos, se requeriría reentrenamiento o un modelo OCR diferente.
*   *Este trabajo forma parte de un proyecto integrador para la Universidad.*

## TODO (Revisado)

*   [ ] **Módulo de Caja**:
    *   [ ] Implementar lógica de cálculo para modalidades `NIGHTLY` y `LONG-TERM` en `cashier-service`.
    *   [ ] Implementar gestión de `ABONO_MENSUAL` (lookup de abonados, cargo cero).
    *   [ ] UI para gestión de abonados.
*   [ ] **ANPR Service**:
    *   [ ] Considerar mecanismo para actualizar cooldown de detección sin reiniciar el servicio.
*   [ ] **Web Portal**:
    *   [ ] Implementar UI para búsqueda de Sesiones de Estacionamiento por patente.
    *   [ ] Implementar columnas ordenables en tablas.
    *   [ ] Mejorar la gestión de errores y feedback al usuario.
    *   [ ] Funcionalidades de reporte y estadísticas.
*   [ ] **Modelos ALPR**:
    *   [ ] Ampliar modelos OCR para mayor robustez o diferentes formatos de patente.
    *   [ ] Optimización de modelos (quantización FP16/INT8, compilación EdgeTPU).
*   [ ] **General**:
    *   [ ] Pruebas unitarias y de integración exhaustivas.
    *   [ ] Documentación de API para `cashier-service`.
    *   [ ] Mejorar la seguridad (ej. gestión de secrets, revisión de permisos).
