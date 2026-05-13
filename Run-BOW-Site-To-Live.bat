@echo off
setlocal

set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

REM One ordered pipeline: STEP1 (copy, STEP3 align, local imports) then STEP2 deploy.
REM Same as: python scripts\run_site_update.py to-live --yes-push
py -3 "%REPO_DIR%scripts\run_site_update.py" to-live --yes-push 2>nul
if errorlevel 9009 python "%REPO_DIR%scripts\run_site_update.py" to-live --yes-push

echo.
echo Finished. Press any key to close.
pause >nul
