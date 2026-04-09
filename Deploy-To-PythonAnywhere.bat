@echo off
setlocal
set "REPO_DIR=%~dp0"
cd /d "%REPO_DIR%"

REM Deploy via Python (SFTP upload + SSH imports). Requires: pip install -r requirements-deploy.txt
REM Same role as before: upload data\imports\raw + app\static, run imports, touch WSGI.
REM Pass flags through, e.g.  --dry-run  --csv-only  --skip-imports  --skip-reload
REM   --venv-bin /home/USER/venv/bin
REM   --wsgi-file "/var/www/BoiledEgg1974_pythonanywhere_com_wsgi.py"
REM Code-only push (no imports): py -3 scripts\pythonanywhere.py sync

py -3 "%REPO_DIR%scripts\pythonanywhere.py" deploy %*
if errorlevel 9009 python "%REPO_DIR%scripts\pythonanywhere.py" deploy %*

echo.
echo Finished. Press any key to close.
pause >nul
