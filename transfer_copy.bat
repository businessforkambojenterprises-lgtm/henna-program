@echo off
rem Copy everything for the move to a new laptop onto a USB drive / external disk.
rem Usage (on the OLD PC):   transfer_copy.bat E:      (E: = the letter of your USB drive)
rem Copies only - nothing on this PC is moved or deleted.
setlocal
if "%~1"=="" ( echo Usage: transfer_copy.bat E:   ^(the drive letter of your USB drive^) & exit /b 1 )
set "DEST=%~1\Ai+d405_transfer"
cd /d "%~dp0"
echo.
echo === Copying to %DEST% ===
mkdir "%DEST%" 2>nul
echo [1/4] Project (code, git history, results, model) without .venv ...
robocopy "%CD%" "%DEST%\Ai+d405" /E /XD .venv __pycache__ /R:1 /W:1 /NFL /NDL /NP
echo [2/4] Recordings in Documents (about 23 GB) ...
robocopy "%USERPROFILE%\Documents" "%DEST%\recordings_Documents" *.db3 *.bag /R:1 /W:1 /NFL /NDL /NP
echo [3/4] Recording archive on D: (about 29 GB, optional history) ...
if exist "D:\d405_recordings_archive" robocopy "D:\d405_recordings_archive" "%DEST%\recordings_archive" /E /R:1 /W:1 /NFL /NDL /NP
echo [4/4] Claude notes and the exported chat ...
robocopy "%USERPROFILE%\.claude\projects\C--Users-DELL-Desktop-Ai-d405\memory" "%DEST%\claude_memory" /E /R:1 /W:1 /NFL /NDL /NP
robocopy "%USERPROFILE%\Downloads" "%DEST%\chat_export" session-export-*.zip /R:1 /W:1 /NFL /NDL /NP
echo.
echo Finished. Check the robocopy summaries above for FAILED counts (should be 0).
echo On the new laptop follow Ai+d405\NEW_LAPTOP_SETUP.md
endlocal
