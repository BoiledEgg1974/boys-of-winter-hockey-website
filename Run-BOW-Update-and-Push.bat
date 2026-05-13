@echo off
setlocal

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

REM Uses scripts\saved_game_csv_paths.json as last-known locations (STEP1).
REM The Python script will ask:
REM   Have your saved-game CSV paths changed? [y/N]
REM - If No: uses saved locations.
REM - If Yes: prompts for new per-league paths and saves them.

py -3 "%REPO_DIR%scripts\run_site_update.py" local --yes-push 2>nul
if errorlevel 9009 python "%REPO_DIR%scripts\run_site_update.py" local --yes-push

echo.
echo Finished. Press any key to close.
pause >nul

