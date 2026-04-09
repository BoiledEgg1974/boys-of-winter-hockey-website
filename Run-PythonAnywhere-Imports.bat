@echo off
setlocal EnableExtensions
REM ============================================================================
REM  Run database imports on PythonAnywhere + reload the web app (via WSGI touch).
REM
REM  This does NOT upload files. Upload CSVs first:
REM    PythonAnywhere website → Files → your project → data/imports/raw/...
REM    (and app/static if you changed images/CSS)
REM
REM  Requires: Windows OpenSSH client (ssh.exe). Same SSH key setup as "ssh user@host".
REM  Edit the variables below if your paths differ.
REM ============================================================================

set "PA_USER=BoiledEgg1974"
set "REMOTE_PROJECT=/home/BoiledEgg1974/boys-of-winter-hockey-website"

REM Venv: use ONE of these (comment the other). Default is home-folder venv.
set "REMOTE_VENV_BIN=/home/BoiledEgg1974/venv/bin"
REM set "REMOTE_VENV_BIN=/home/BoiledEgg1974/boys-of-winter-hockey-website/venv/bin"

REM WSGI file PA uses to trigger reload — change if your Web tab shows a different path.
set "WSGI_FILE=/var/www/BoiledEgg1974_wsgi.py"
REM set "WSGI_FILE=/var/www/BoiledEgg1974_pythonanywhere_com_wsgi.py"

set "REMOTE=%PA_USER%@ssh.pythonanywhere.com"

cd /d "%~dp0"

echo.
echo === PythonAnywhere remote imports ===
echo Upload CSVs via the Files tab first, then press any key to run imports + reload...
pause >nul
echo.

where ssh >nul 2>&1
if errorlevel 1 (
  echo ERROR: ssh.exe not found. Install "OpenSSH Client" in Windows Optional Features.
  exit /b 1
)

REM One remote bash session: cd, venv, three imports, touch WSGI
ssh "%REMOTE%" bash -lc "set -e; cd '%REMOTE_PROJECT%'; . '%REMOTE_VENV_BIN%/activate'; export LEAGUE_SLUG=bowl-historical; python scripts/import_data.py; export LEAGUE_SLUG=bowl-fantasy; python scripts/import_data.py; export LEAGUE_SLUG=bowl-cap; python scripts/import_data.py; touch '%WSGI_FILE%'"

if errorlevel 1 (
  echo.
  echo FAILED. Check: SSH key works ^(try: ssh %REMOTE%^), REMOTE_VENV_BIN, WSGI_FILE.
  exit /b 1
)

echo.
echo Done. If the site still looks old, open Web tab and click Reload.
echo.
pause
