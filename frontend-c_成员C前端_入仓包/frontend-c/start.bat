@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

echo ============================================
echo   群忆 / frontend-c   成员 C 前端
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [!] 没找到 python 命令。
  echo     请安装 Python 3.10+ 并勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo 正在启动前端： http://127.0.0.1:5173
echo 后端请另开一个终端，在 member-d\backend 里执行：  python -m qunyi
echo 关闭这个窗口即停止前端。
echo.

python serve.py %*
pause
