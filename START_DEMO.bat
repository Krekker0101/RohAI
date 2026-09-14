@echo off
setlocal
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo [Smart Traffic AI] uv is not installed or not in PATH.
  echo Install uv, then run this file again.
  pause
  exit /b 1
)
set "STA_MODE=simulation"
set "STA_POLICY=adaptive"
set "STA_TICK_SECONDS=0.25"
start "Smart Traffic AI Backend" cmd /k "uv run uvicorn smart_traffic_backend.main:app --host 127.0.0.1 --port 8000 --workers 1"
for /l %%I in (1,1,30) do (
  curl.exe -fsS http://127.0.0.1:8000/health/demo-ready >nul 2>nul && goto ready
  timeout /t 1 /nobreak >nul
)
echo [Smart Traffic AI] Backend did not become ready. Check the backend window.
pause
exit /b 2
:ready
start "" "http://127.0.0.1:8000/dashboard/"
echo.
echo DEMO opened. Use the DEMO/REAL switch in the top bar.
echo Close the backend terminal when the presentation is finished.
endlocal
