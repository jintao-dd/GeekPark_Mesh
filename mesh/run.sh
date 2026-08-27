#!/usr/bin/env bash
# 一键本地运行：./run.sh   （首次会自动装依赖）
set -e
cd "$(dirname "$0")"
if [ ! -f .env ]; then cp .env.example .env; echo ">> 已生成 .env，请先填写 ANTHROPIC_API_KEY 等，再重新运行"; exit 1; fi
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
pip install -q -r requirements.txt
mkdir -p data/raw
exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}
