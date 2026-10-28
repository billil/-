@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo 正在创建本地 Python 环境...
    python -m venv .venv
    if errorlevel 1 goto :error
)

if not exist ".venv\.dependencies-installed" (
    echo 正在安装依赖...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :error
    type nul > ".venv\.dependencies-installed"
)

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo 已创建 .env，请填写 QQ_APP_ID 和 QQ_APP_SECRET 后重新启动。
    pause
    exit /b 1
)

".venv\Scripts\python.exe" qq_group_bot.py
if errorlevel 1 goto :error
exit /b 0

:error
echo.
echo 启动失败，请查看上方错误信息。
pause
exit /b 1
