@echo off
setlocal
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  echo [x] python not found. Install Python 3.10+ and add it to PATH.
  pause
  exit /b 1
)
echo Qunyi UI  http://127.0.0.1:5173
echo Backend must be running: cd member-d\backend ^&^& python -m qunyi
echo.
python serve.py %*
pause
