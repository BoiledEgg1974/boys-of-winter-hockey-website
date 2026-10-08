@echo off
setlocal
set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

REM Live deploy (DigitalOcean VPS by default via scripts\deploy-live-vps.env).
REM Same as: python scripts\run_site_update.py deploy
REM Legacy PA: set BOWL_DEPLOY_TARGET=pa and PA_HOST=ssh.pythonanywhere.com first.
REM Flags: --dry-run  --csv-only  --skip-imports  --skip-reload  --remote-pip
REM Code-only sync: py -3 scripts\STEP2_pythonanywhere.py sync

py -3 "%REPO_DIR%scripts\run_site_update.py" deploy %*
if errorlevel 9009 python "%REPO_DIR%scripts\run_site_update.py" deploy %*

echo.
echo Finished. Press any key to close.
pause >nul
