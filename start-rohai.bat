@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo RohAI: project Python environment is missing. Install the project dependencies first.
  pause
  exit /b 2
)
".venv\Scripts\python.exe" -m smart_traffic_backend.launcher
set "ROHAI_EXIT_CODE=%ERRORLEVEL%"
if not "%ROHAI_EXIT_CODE%"=="0" pause
exit /b %ROHAI_EXIT_CODE%
