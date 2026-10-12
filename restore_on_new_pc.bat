@echo off
rem Put the Ai+d405 move package in place on the NEW PC. Run it from the extracted Ai+d405_move folder.
rem Copies only - nothing is deleted. Stops if the project or the app is already on this PC.
rem It does not connect to the CNC or the camera.
setlocal
set "SRC=%~dp0"
set "DESK=%USERPROFILE%\Desktop"
set "KEY=%DESK%\Ai+d405"
set "KEY=%KEY::=-%"
set "KEY=%KEY:\=-%"
set "KEY=%KEY:+=-%"
echo.
echo === Restore Ai+d405 on this PC (user %USERNAME%) ===
if /i not "%USERNAME%"=="DELL" echo NOTE: this Windows user is not DELL - old jobs will not reopen; new jobs work normally.
if not exist "%SRC%Ai+d405\CLAUDE.md" ( echo STOP: run this from the extracted Ai+d405_move folder. & pause & exit /b 1 )
if exist "%DESK%\Ai+d405\" ( echo STOP: %DESK%\Ai+d405 already exists - nothing was copied. & pause & exit /b 1 )
if exist "%DESK%\HennaStudio-Windows-x64\" ( echo STOP: %DESK%\HennaStudio-Windows-x64 already exists - nothing was copied. & pause & exit /b 1 )

echo [1/5] Project -^> %DESK%\Ai+d405
robocopy "%SRC%Ai+d405" "%DESK%\Ai+d405" /E /R:1 /W:1 /NFL /NDL /NP /NJH
if errorlevel 8 ( echo FAILED copying the project. & pause & exit /b 1 )

echo [2/5] Desktop app -^> %DESK%\HennaStudio-Windows-x64
robocopy "%SRC%HennaStudio-Windows-x64" "%DESK%\HennaStudio-Windows-x64" /E /R:1 /W:1 /NFL /NDL /NP /NJH
if errorlevel 8 ( echo FAILED copying the desktop app. & pause & exit /b 1 )
if not exist "%DESK%\Henna Studio.lnk" copy "%SRC%Henna Studio.lnk" "%DESK%\" >nul

echo [3/5] Claude notes and chats -^> %USERPROFILE%\.claude\projects\%KEY%  (existing files kept)
robocopy "%SRC%claude_project" "%USERPROFILE%\.claude\projects\%KEY%" /E /XC /XN /XO /R:1 /W:1 /NFL /NDL /NP /NJH
if errorlevel 8 ( echo FAILED copying the Claude notes. & pause & exit /b 1 )

echo [4/5] Camera recordings -^> %USERPROFILE%\Documents  (existing files kept)
if exist "%SRC%recordings\" (
  robocopy "%SRC%recordings" "%USERPROFILE%\Documents" *.db3 /XC /XN /XO /R:1 /W:1 /NFL /NDL /NP /NJH
  if errorlevel 8 ( echo FAILED copying the recordings. & pause & exit /b 1 )
) else (
  echo No recordings folder - skipped. Extract the recordings zip into the same place to add them later.
)

echo [5/5] Removing the "downloaded from the internet" mark from the copied files ...
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%DESK%\Ai+d405','%DESK%\HennaStudio-Windows-x64' -Recurse -File -Force | Unblock-File"

echo.
echo Done. Next: install Python 3.11.9 if needed, then double-click %DESK%\Ai+d405\setup_new_laptop.bat
pause
