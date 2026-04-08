@echo off
setlocal
cd /d "%~dp0.."
call "%~dp0import_historical.cmd"
if errorlevel 1 exit /b 1
call "%~dp0import_fantasy.cmd"
if errorlevel 1 exit /b 1
call "%~dp0import_cap.cmd"
if errorlevel 1 exit /b 1
exit /b 0
