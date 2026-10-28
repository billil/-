#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [[ ! -x .venv/bin/python ]]; then
  echo "正在创建本地 Python 环境..."
  python3 -m venv .venv
fi

if [[ ! -f .venv/.dependencies-installed ]]; then
  echo "正在安装依赖..."
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.dependencies-installed
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "已创建 .env，请填写 QQ_APP_ID 和 QQ_APP_SECRET 后重新启动。"
  exit 1
fi

exec .venv/bin/python qq_group_bot.py
