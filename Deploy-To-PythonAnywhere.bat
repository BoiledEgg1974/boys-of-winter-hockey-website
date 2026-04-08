@echo off
setlocal
set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

REM Default: upload CSVs + static, run imports, touch WSGI to reload.
REM Examples:
REM   powershell -ExecutionPolicy Bypass -File scripts\deploy_pythonanywhere.ps1 -SkipReload
REM   powershell -ExecutionPolicy Bypass -File scripts\deploy_pythonanywhere.ps1 -WsgiFile "/var/www/BoiledEgg1974_pythonanywhere_com_wsgi.py"

powershell -NoProfile -ExecutionPolicy Bypass -File "%REPO_DIR%scripts\deploy_pythonanywhere.ps1" %*

echo.
echo Finished. Press any key to close.
pause >nul
