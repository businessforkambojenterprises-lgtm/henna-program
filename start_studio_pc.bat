@echo off
rem Henna Studio v29 on this PC, from the project source (user 2026-10-24: "we'll run it from pc from now"),
rem with the Pi's home page and Admin page (webui_server_pc.py; user 2026-10-24: "same home page and admin page").
rem Uses the latest Studio code and this project's machine calibration (machines\) - NOT the packaged
rem HennaStudio.exe builds of 2026-10-16, which have their own old data folder and leave out the travel-path checks.
rem Stop Studio with Exit in Studio, or Ctrl+C in this window (a moving machine is stopped and the CNC disconnected first).
cd /d "%~dp0"
if not exist .venv\Scripts\python.exe (
  echo The Python environment is missing - run setup_new_laptop.bat first.
  pause
  exit /b 1
)
title Henna Studio v29 (PC) - press Ctrl+C here to stop
start "" cmd /c "timeout /t 4 /nobreak >nul & start "" http://127.0.0.1:8765/"
.venv\Scripts\python.exe webui_server_pc.py
rem 42 = closed with the Exit button in Studio (camera off, CNC disconnected): close this window too
if "%errorlevel%"=="42" exit 0
echo.
echo Henna Studio has stopped.
pause
