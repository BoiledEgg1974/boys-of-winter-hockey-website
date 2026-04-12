@echo off
setlocal

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

python "scripts\STEP1_update_from_saved_game.py"

echo.
echo Finished. Press any key to close.
pause >nul

