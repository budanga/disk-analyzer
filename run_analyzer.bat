@echo off
title AI Disk Analyzer

:: If you prefer not to set the environment variable globally,
:: you can uncomment the next line and write your API key:
:: set GEMINI_API_KEY=your_api_key_here

echo Starting parallel disk analysis...
python "%~dp0disk_analyzer.py"

if %ERRORLEVEL% neq 0 (
    echo 
    echo An error occurred while running the script.
    pause
)
