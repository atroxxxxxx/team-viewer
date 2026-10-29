@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo Run: py -3 scripts\dev.py setup --postgres
    exit /b 1
)
".venv\Scripts\python.exe" scripts\dev.py %*
