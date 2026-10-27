@echo off
REM 一键初始化数据库 + 导入示例数据 + 启动服务（Windows）
REM 用法：在 backend 目录双击，或在任意目录执行 scripts\run.bat
cd /d "%~dp0.."

python -m scripts.seed_demo
if errorlevel 1 (
    echo [ERROR] 初始化失败，请检查 Python 依赖是否安装
    exit /b 1
)

echo 启动服务：http://127.0.0.1:8000/api/health
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
