@echo off
REM Release analytics dashboard. Pass --watch 300 to refresh continuously.
setlocal
cd /d "%~dp0"
python tools\stats.py %*
if errorlevel 1 pause
endlocal
