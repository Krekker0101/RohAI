@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Prepare dependencies first: uv sync --extra dev --extra vision --extra learning
  pause
  exit /b 1
)
if not exist "apps\dashboard\dist\index.html" (
  echo Build dashboard first: cd apps\dashboard ^& npm ci ^& npm run build
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -m smart_traffic_backend.video_cli
if errorlevel 1 pause
