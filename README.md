# AI Disk Analyzer

A Python-based disk space analysis utility that generates interactive HTML reports and provides intelligent cleanup recommendations powered by Google's Gemini API.

## Features

- **Parallel Scanning**: Rapid directory traversal optimized using multiple threads (`concurrent.futures.ThreadPoolExecutor`) and efficient file scanning using `os.scandir`.
- **Interactive Visual Dashboard**: Generates a high-quality HTML report featuring space usage charts, animated progress rings, and file category breakdowns.
- **AI-Powered Insights**: Integrates Google's Gemini SDK (`google-genai`) to review storage distribution and construct specific space-saving recommendations.
- **Robust Error Handling**: Safely skips system-restricted folders and files with read permissions or I/O errors without interrupting the scanning process.

## Prerequisites

Python 3.8 or higher is required along with the following packages:

```bash
pip install google-genai psutil
```

## Configuring the Gemini API Key

To enable intelligent cleanup recommendations, you must configure a Gemini API key. You can get one for free at [Google AI Studio](https://aistudio.google.com/apikey).

You can configure the key in one of two ways:

### Option 1: Environment Variable (Recommended)
Set the `GEMINI_API_KEY` environment variable in your system session:

- **Windows (PowerShell)**:
  ```powershell
  $env:GEMINI_API_KEY="your_api_key_here"
  ```
- **Windows (CMD)**:
  ```cmd
  set GEMINI_API_KEY=your_api_key_here
  ```
- **Linux/macOS**:
  ```bash
  export GEMINI_API_KEY="your_api_key_here"
  ```

### Option 2: Script Editing
Edit the `disk_analyzer.py` script and replace `"YOUR_API_KEY_HERE"` with your API key:

```python
API_KEY = os.environ.get("GEMINI_API_KEY", "your_api_key_here")
```

## Usage

Run the script from your terminal:

```bash
python disk_analyzer.py
```

Upon completion:
1. The script creates a `disk-analyzer-reports` folder inside the project directory (if it does not exist).
2. It saves the HTML report with the format `disk_report_YYYYMMDD_HHMMSS.html` in that folder.
3. The report is automatically opened in your default web browser.
