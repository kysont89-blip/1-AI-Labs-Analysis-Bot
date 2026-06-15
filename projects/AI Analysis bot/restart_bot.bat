@echo off
echo ==========================================
echo    AI Analysis Bot - Restart Script
echo ==========================================
echo.

:: Kill any running python processes
echo [1/4] Killing old bot instances...
taskkill /F /IM python.exe 2>nul
taskkill /F /IM pythonw.exe 2>nul
echo.

:: Wait for Telegram API to release the lock
echo [2/4] Waiting 60 seconds for Telegram API to clear...
echo      (DO NOT close this window)
timeout /t 60 /nobreak >nul
echo.

:: Clear old log
echo [3/4] Clearing old log...
if exist "bot_log.txt" del "bot_log.txt"
echo.

:: Start fresh bot
echo [4/4] Starting bot with NEW code...
echo      (sanitized reports + fixed upgrade button)
echo.
cd /d "%~dp0"
python bots\telegram_bot_v2.py

pause
