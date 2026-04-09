@echo off
setlocal
cd /d "%~dp0"
REM GUI app — no console window needed if pythonw exists
where pythonw >nul 2>&1 && start "" pythonw "%~dp0scripts\make_league_csv_zips.py" && exit /b 0
python "%~dp0scripts\make_league_csv_zips.py"
if errorlevel 1 pause
