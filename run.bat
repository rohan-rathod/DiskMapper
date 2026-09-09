@echo off
REM DiskMapper launcher - starts the GUI without a console window if possible.
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw main.py %*
) else (
    python main.py %*
)
