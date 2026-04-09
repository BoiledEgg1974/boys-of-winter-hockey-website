@echo off
setlocal
cd /d "%~dp0.."
set LEAGUE_SLUG=bowl-historical
python "%~dp0import_data.py"
exit /b %ERRORLEVEL%
