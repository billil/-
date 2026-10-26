#!/usr/bin/env bash
# 一键初始化数据库 + 导入示例数据 + 启动服务（macOS / Linux / Git Bash）
set -e
cd "$(dirname "$0")/.."

python -m scripts.seed_demo
echo "启动服务：http://127.0.0.1:8000/api/health"
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
