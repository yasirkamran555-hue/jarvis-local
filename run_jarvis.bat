@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo JARVIS is not installed yet. Run install.ps1 first.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" desktop_app.py
if errorlevel 1 (
    echo JARVIS exited with an error.
    pause
)
