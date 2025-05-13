# Explicación del Funcionamiento del Sistema ALPR (Automatic License Plate Recognition)

Este documento describe el flujo de trabajo y los componentes principales del sistema de reconocimiento automático de patentes vehiculares implementado en este proyecto.

## 1. Introducción

El objetivo principal del sistema es detectar y reconocer los caracteres de las patentes de vehículos a partir de una fuente de video o una imagen estática. El proceso se divide en varias etapas: carga de configuración, lectura de la fuente, detección de la patente, reconocimiento de caracteres (OCR) y guardado de resultados.

## 2. Flujo Principal (`reconocedor_automatico.py`)

El script `reconocedor_automatico.py` actúa como el orquestador principal del sistema.

*   **Inicio y Configuración:**
    *   Al ejecutarse, utiliza `ArgumentParser` para procesar argumentos de línea de comandos, como la ruta al archivo de configuración (`--cfg`), si se debe ejecutar en modo demostración (`--demo`), si guardar el video resultante (`--guardar_video`) o si medir el rendimiento (`--benchmark`).
    *   Carga la configuración desde un archivo YAML (por defecto `config.yaml`) usando `yaml.safe_load(stream)`. Esta configuración contiene parámetros cruciales como los modelos a usar, umbrales de confianza, fuente de video, etc.
    *   Inicializa la clase principal `ALPR` pasándole la configuración cargada: `alpr = ALPR(cfg['modelo'], cfg['db'])`.

*   **Procesamiento de Video/Imagen:**
    *   Abre la fuente de video o imagen especificada en la configuración (`cfg['video']['fuente']`) usando `cv2.VideoCapture(video_path)`.
    *   Entra en un bucle (`while True`) para leer frames de la fuente (`cap.read()`). Si la fuente es una imagen, el bucle se ejecuta una sola vez (`is_img = cv2.haveImageReader(video_path)`).
    *   Maneja posibles errores de lectura del stream (especialmente para cámaras IP) reintentando la conexión.

*   **Modos de Operación:**
    *   **Modo Demo (`if demo:`):**
        *   Llama a `alpr.mostrar_predicts(frame)` que procesa el frame y devuelve una copia con las detecciones (rectángulos y texto) dibujadas.
        *   Muestra el frame procesado en una ventana (`cv2.imshow("result", frame_w_pred_r)`).
        *   Si está activado `--benchmark`, muestra el tiempo de procesamiento y los FPS en el frame.
        *   Si está activado `--guardar_video`, escribe el frame procesado en un archivo `alpr-result.avi`.
    *   **Modo Procesamiento (`else:`):**
        *   Procesa frames a intervalos definidos por `cfg['video']['frecuencia_inferencia']` para optimizar el rendimiento (`if frame_id % intervalo_reconocimiento == 0:`).
        *   Llama al método principal de reconocimiento: `patentes = alpr.predict(frame)`.
        *   Si se detectan patentes (`if patentes:`):
            *   Registra la hora y las patentes detectadas usando `logging`.
            *   Evita registrar la misma patente consecutivamente comparando con `last_seen_patentes`.
            *   Guarda cada patente detectada junto con la marca de tiempo en un archivo CSV (`alpr-results.csv`).
            *   Guarda el frame donde se detectó la patente como una imagen PNG en la carpeta `./alpr-results/`.
        *   Si está activado `--benchmark`, imprime el tiempo de procesamiento y los FPS en la consola.

*   **Finalización:** Libera los recursos de video (`cap.release()`, `out.release()`) y cierra las ventanas de OpenCV (`cv2.destroyAllWindows()`).

## 3. Clase Principal ALPR (`alpr/alpr.py`)

La clase `ALPR` encapsula la lógica central de detección y reconocimiento.

*   **Inicialización (`__init__`):**
    *   Hereda de `SqlSaver` para la funcionalidad de guardado en base de datos.
    *   Carga el modelo de **detección** (`PlateDetector`) especificado en `cfg['modelo']['resolucion_detector']`. Valida que la resolución sea una de las soportadas (384, 512, 608).
    *   Carga el modelo de **OCR** (`PlateOCR`) especificado por `cfg['modelo']['numero_modelo_ocr']` y establece los umbrales de confianza (`confianza_avg_ocr`, `confianza_low_ocr`).
    *   Configura si se guardarán los resultados en la base de datos (`self.guardar_bd = cfg_db['guardar']`).

*   **Método `predict`:**
    *   Orquesta el proceso de reconocimiento estándar:
        1.  Preprocesa el frame de entrada para el detector: `input_img = self.detector.preprocess(frame)`.
        2.  Realiza la inferencia con el modelo YOLO para detectar patentes: `yolo_out = self.detector.predict(input_img)`.
        3.  Aplica Non-Max Suppression (NMS) para filtrar detecciones redundantes: `bboxes = self.detector.procesar_salida_yolo(yolo_out)`.
        4.  Obtiene las coordenadas de los rectángulos detectados: `iter_coords = self.detector.yield_coords(frame, bboxes)`.
        5.  Realiza el OCR sobre cada rectángulo detectado: `patentes = self.ocr.predict(iter_coords, frame)`.
        6.  Si `self.guardar_bd` es `True`, actualiza las patentes detectadas en memoria para su posterior guardado en la BD: `self.update_in_memory(patentes)`.
    *   Devuelve una lista con las patentes reconocidas como texto.

*   **Método `mostrar_predicts`:**
    *   Utilizado en el modo demo. Sigue un flujo similar a `predict` pero, en lugar de solo devolver el texto, dibuja los rectángulos (`cv2.rectangle`) y el texto reconocido (`cv2.putText`) directamente sobre el frame para visualización.
    *   Calcula y devuelve el tiempo total de procesamiento (`total_time`).

## 4. Detección de Patentes (`alpr/detector.py`)

La clase `PlateDetector` se encarga de localizar las patentes en la imagen.

*   **Inicialización (`__init__`):**
    *   Carga el modelo YOLOv4-tiny pre-entrenado (en formato TensorFlow SavedModel) desde la ruta especificada (`weights_path`).
    *   Establece los umbrales para NMS (`iou`) y la confianza mínima de detección (`score`).

*   **Método `preprocess`:**
    *   Redimensiona la imagen de entrada al tamaño requerido por el modelo YOLO (`self.input_size`).
    *   Normaliza los valores de los píxeles al rango [0, 1].
    *   Añade una dimensión de batch para que coincida con la entrada esperada por el modelo.

*   **Método `predict`:**
    *   Ejecuta la inferencia del modelo YOLO (`self.yolo_infer(input_img)`) sobre la imagen preprocesada.

*   **Método `procesar_salida_yolo`:**
    *   Aplica la función `tf.image.combined_non_max_suppression` a la salida cruda del modelo YOLO. Esto elimina rectángulos superpuestos que probablemente correspondan a la misma patente, conservando solo la detección más confiable.

*   **Método `yield_coords`:**
    *   Itera sobre los rectángulos resultantes del NMS.
    *   Convierte las coordenadas normalizadas (proporcionadas por el modelo) a coordenadas de píxeles absolutas basadas en las dimensiones del frame original (`image_h`, `image_w`).
    *   Devuelve (yield) las coordenadas `(x1, y1, x2, y2)` y la puntuación de confianza (`score`) para cada patente detectada.

## 5. Reconocimiento de Caracteres (OCR) (`alpr/ocr.py`)

La clase `PlateOCR` se especializa en identificar los caracteres dentro de los rectángulos detectados.

*   **Inicialización (`__init__`):**
    *   Carga el modelo CNN de OCR (TensorFlow SavedModel) especificado por `ocr_model_num` (1 a 4).
    *   Define el alfabeto de caracteres posibles (`self.alphabet = string.digits + string.ascii_uppercase + '_'`).
    *   Establece los umbrales de confianza promedio (`confianza_avg`) y mínima por caracter (`none_low_thresh`) para validar una predicción.

*   **Método `predict`:**
    *   Recibe el iterador de coordenadas (`iter_coords`) del detector.
    *   Para cada rectángulo (`yolo_prediction`):
        *   Llama a `predict_ocr` para obtener el texto y las probabilidades de cada caracter.
        *   Valida la predicción:
            *   Calcula la confianza promedio (`avg = np.mean(probs)`).
            *   Verifica que ningún caracter tenga una confianza individual por debajo del umbral `none_low_thresh` usando `self.none_low(probs, ...)`.
            *   Si ambas condiciones se cumplen, limpia el texto (quita `_`) y lo añade a la lista `patentes`.
    *   Devuelve la lista de patentes validadas.

*   **Método `predict_ocr`:**
    *   Recorta la región de la patente del frame original usando las coordenadas `(x1, y1, x2, y2)`.
    *   Llama a `__predict_from_array` para realizar la inferencia sobre la imagen recortada.
    *   Llama a `__probs_to_plate` para convertir la salida del modelo en texto y probabilidades.

*   **Método `__predict_from_array`:**
    *   Preprocesa la imagen recortada de la patente:
        *   Convierte a escala de grises (`cv2.cvtColor`).
        *   Redimensiona a (140, 70) (`cv2.resize`).
        *   Añade dimensiones de batch y canal (`np.newaxis`).
        *   Normaliza los píxeles a [0, 1].
    *   Realiza la inferencia con el modelo OCR (`self.cnn_ocr_model`).

*   **Método `__probs_to_plate`:**
    *   Remodela la salida del modelo a una matriz (7 caracteres x 37 posibles clases).
    *   Encuentra el caracter más probable para cada una de las 7 posiciones (`np.argmax`).
    *   Obtiene la probabilidad máxima para cada posición (`np.max`).
    *   Mapea los índices predichos a caracteres usando `self.alphabet`.
    *   Devuelve la lista de caracteres (`plate`) y la lista de sus probabilidades (`probs`).

## 6. Guardado de Resultados (`alpr/saver.py`)

La clase `SqlSaver` gestiona el almacenamiento de las patentes reconocidas en una base de datos SQLite.

*   **Inicialización (`__init__`):**
    *   Establece la frecuencia con la que se insertarán datos en la BD (`frequency_insert`).
    *   Crea la carpeta contenedora de la BD si no existe (`Path(db_path.parent).mkdir`).
    *   Establece la conexión con la base de datos SQLite (`sqlite3.connect(db_path)`).
    *   Crea la tabla `plates` si no existe.
    *   Mantiene un conjunto (`self.unique_plates`) en memoria para almacenar temporalmente las patentes únicas detectadas.

*   **Método `update_in_memory`:**
    *   Añade las nuevas patentes detectadas (`plates`) al conjunto `self.unique_plates`. Como es un conjunto, las duplicadas se ignoran automáticamente.
    *   Si el tamaño del conjunto supera `self.frequency_insert`, llama a `insert_in_disk` para escribir los datos en la BD y luego limpia el conjunto (`self.unique_plates.clear()`).

*   **Método `insert_in_disk`:**
    *   Inserta todas las patentes del conjunto `self.unique_plates` en la tabla `plates` de la base de datos usando `cursor.executemany`.
    *   Confirma la transacción (`self.conn.commit()`).

*   **Método `__del__` (Destructor):**
    *   Asegura que cualquier cambio pendiente se guarde (`self.conn.commit()`) y cierra la conexión a la base de datos (`self.conn.close()`) cuando el objeto `ALPR` es destruido.

## 7. Configuración (`config.yaml`)

El archivo `config.yaml` es fundamental, ya que permite ajustar el comportamiento del sistema sin modificar el código. Define parámetros como:

*   Ruta de la fuente de video/imagen.
*   Modelos específicos de detección y OCR a utilizar.
*   Umbrales de confianza para la detección y el OCR.
*   Frecuencia de inferencia en videos.
*   Configuración de la base de datos (ruta, frecuencia de inserción, si se debe guardar o no).

## 8. Conclusión

El sistema integra un modelo de detección de objetos (YOLOv4-tiny) para localizar patentes y un modelo de reconocimiento de caracteres (CNN) para leerlas. El flujo principal gestiona la entrada de datos, orquesta la detección y el OCR a través de la clase `ALPR`, y maneja el guardado de resultados en CSV, imágenes y/o una base de datos SQLite, todo configurable mediante un archivo YAML.
