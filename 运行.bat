@echo off
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found! Please install Python 3.10+
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
python launcher.py
pause
