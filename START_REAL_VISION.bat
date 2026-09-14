@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo [Smart Traffic AI] uv is not installed or not in PATH.
  pause
  exit /b 1
)
if not exist "recordings\car-detection.mp4" (
  echo [Smart Traffic AI] recordings\car-detection.mp4 is missing.
  echo Run: uv run --extra vision python scripts\fetch_vision_demo.py --asset video
  pause
  exit /b 2
)
set "STA_MODE=vision"
set "STA_POLICY=adaptive"
set "STA_TICK_SECONDS=0.1"
set "STA_VISION__SOURCE=file"
set "STA_VISION__SOURCE_ID=intel-car"
set "STA_VISION__URI=recordings/car-detection.mp4"
set "STA_VISION__GEOMETRY_PATH=configs/cameras/intel-car.json"
set "STA_VISION__MODEL_PATH=datasets/models/yolo26n.pt"
set "STA_VISION__DEVICE=cpu"
start "Smart Traffic AI REAL Vision" cmd /k "uv run --extra vision uvicorn smart_traffic_backend.main:app --host 127.0.0.1 --port 8000 --workers 1"
for /l %%I in (1,1,45) do (
  curl.exe -fsS http://127.0.0.1:8000/health/ready >nul 2>nul && goto ready
  timeout /t 1 /nobreak >nul
)
echo [Smart Traffic AI] Backend did not start. Check the REAL Vision window.
pause
exit /b 3
:ready
start "" "http://127.0.0.1:8000/dashboard/"
echo.
echo Dashboard opened. Click REAL in the top bar for actual Vision telemetry.
endlocal
