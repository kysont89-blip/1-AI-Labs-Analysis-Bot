@echo off
cd /d %~dp0
echo ==========================================
echo    AI Analysis Bot - Interactive Flow
echo ==========================================
echo.

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.11+
    pause
    exit /b 1
)

:: Install deps if needed
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating environment...
call venv\Scripts\activate.bat

echo Installing dependencies...
pip install -q -r requirements.txt

echo.
echo ==========================================
echo  Starting Telegram Bot v2 (interactive flow)
echo ==========================================
python bots\telegram_bot_v2.py

pause
