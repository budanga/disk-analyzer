# Analizador de Disco con IA

Herramienta en Python para el análisis de espacio en disco que genera reportes interactivos en formato HTML y proporciona recomendaciones inteligentes de limpieza mediante la API de Gemini de Google.

## Características

- **Análisis paralelo**: Traversal rápido de directorios optimizado mediante el uso de múltiples hilos (`concurrent.futures.ThreadPoolExecutor`) y lectura eficiente con `os.scandir`.
- **Reporte visual interactivo**: Genera un dashboard en formato HTML autocompletado con gráficos de consumo, diagramas de anillo y desglose por categorías.
- **Integración con IA**: Utiliza el SDK de Gemini (`google-genai`) para analizar la estructura de archivos del sistema y formular planes de acción específicos para liberar espacio.
- **Robustez**: Omite de forma segura los archivos y directorios con restricciones de permisos u otros errores de lectura de E/S.

## Requisitos previos

Es necesario contar con Python 3.8 o superior y las siguientes bibliotecas:

```bash
pip install google-genai psutil
```

## Configuración de la API Key de Gemini

Para utilizar el análisis inteligente con la IA de Gemini, es necesario configurar una clave de API. Puede obtener una de forma gratuita en [Google AI Studio](https://aistudio.google.com/apikey).

Existen dos opciones para configurar la clave:

### Opción 1: Variable de entorno (Recomendada)
Configure la variable de entorno `GEMINI_API_KEY` con su clave de API:

- **Windows (PowerShell)**:
  ```powershell
  $env:GEMINI_API_KEY="su_clave_aqui"
  ```
- **Windows (CMD)**:
  ```cmd
  set GEMINI_API_KEY=su_clave_aqui
  ```
- **Linux/macOS**:
  ```bash
  export GEMINI_API_KEY="su_clave_aqui"
  ```

### Opción 2: Edición del script
Modifique el archivo `disk_analyzer.py` y reemplace la cadena `"TU_API_KEY_AQUI"` en la sección de configuración:

```python
API_KEY = os.environ.get("GEMINI_API_KEY", "su_clave_aqui")
```

## Uso

Ejecute el script desde la línea de comandos:

```bash
python disk_analyzer.py
```

Al finalizar el análisis, el script:
1. Creará un directorio llamado `disk-analyzer-reports` dentro del directorio del proyecto (si no existe).
2. Guardará el reporte HTML con el formato `reporte_disco_YYYYMMDD_HHMMSS.html` en dicha carpeta.
3. Abrirá automáticamente el reporte en el navegador web predeterminado.
