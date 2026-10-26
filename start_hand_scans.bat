@echo off
rem Hand scans with the D405 on a stand - web page (hand_scan_web.py). Close Henna Studio first.
cd /d "%~dp0"
title Hand scans - close this window to stop
start "" cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8770/"
.venv\Scripts\python.exe hand_scan_web.py
echo.
pause
