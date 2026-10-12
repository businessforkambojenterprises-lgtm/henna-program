@echo off
rem Set up the Ai+d405 project on a new PC: Python environment + packages + hand model + self-tests.
rem Run it from the project folder (double-click). Needs Python 3.11 installed first.
rem It does not connect to the CNC or the camera.
cd /d "%~dp0"
echo.
echo === Ai+d405 setup in %CD% ===
py -3.11 --version >nul 2>&1
if errorlevel 1 (
  echo Python 3.11 was not found. Install Python 3.11.9 64-bit from python.org first ^(tick "Add python.exe to PATH"^).
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
echo.
echo Checking the hand model ...
.venv\Scripts\python.exe setup_hand_model.py
if errorlevel 1 ( echo The hand model is missing or wrong - see above. & pause & exit /b 1 )
echo.
echo Running the self-tests (no hardware) ...
.venv\Scripts\python.exe -m unittest discover -s tests
if errorlevel 1 ( echo SELF-TESTS FAILED - do not use the machine until this is solved. & pause & exit /b 1 )
pushd desktop_build
..\.venv\Scripts\python.exe -m unittest discover -p "test_*.py"
if errorlevel 1 ( popd & echo DESKTOP TESTS FAILED - see above. & pause & exit /b 1 )
popd
echo.
echo Done - all tests OK. Start Studio v21 from source with:
echo    .venv\Scripts\python.exe webui_server_v21.py
echo and open http://127.0.0.1:8765  (or use the desktop app: Desktop\HennaStudio-Windows-x64\HennaStudio.exe)
pause
