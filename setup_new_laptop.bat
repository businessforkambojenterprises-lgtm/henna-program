@echo off
rem Set up the Ai+d405 project on a new laptop: Python environment + packages + self-test.
rem Run it from the project folder (double-click). Needs Python 3.11 installed first.
cd /d "%~dp0"
echo.
echo === Ai+d405 setup in %CD% ===
py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 was not found. Install Python 3.11.9 from python.org first ^(tick "Add python.exe to PATH"^).
  pause
  exit /b 1
)
if not exist .venv (
  echo Creating the Python environment .venv ...
  py -3.11 -m venv .venv
  if errorlevel 1 ( echo Could not create .venv & pause & exit /b 1 )
)
echo Installing the exact package versions from requirements-lock.txt ...
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
if errorlevel 1 ( echo Package installation failed - see the messages above. & pause & exit /b 1 )
if not exist models\hand_landmarker.task echo WARNING: models\hand_landmarker.task is missing - copy it from the transfer package.
echo.
echo Running the self-tests ...
.venv\Scripts\python.exe -m unittest discover -s tests
echo.
echo Done. Start the studio with:   .venv\Scripts\python.exe webui_server_v12.py
pause
