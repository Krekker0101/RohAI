@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" -m traffic_vision.cli maket start --open
pause
