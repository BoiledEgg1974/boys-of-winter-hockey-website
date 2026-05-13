@echo off
setlocal

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

py -3 "%REPO_DIR%scripts\run_site_update.py" local 2>nul
if errorlevel 9009 python "%REPO_DIR%scripts\run_site_update.py" local

echo.
echo Finished. Press any key to close.
pause >nul

