@echo off
cd /d "%~dp0"
python --version >nul 2>&1
if errorlevel 1 (
    echo Python not found! Please install Python 3.10+
    echo https://www.python.org/downloads/
    pause
    exit /b 1
)
echo.
echo   AI Speech Coach Web Server
echo   http://localhost:8000
echo   Press Ctrl+C to stop
echo.
start "" http://localhost:8000
python run_server.py
pause
