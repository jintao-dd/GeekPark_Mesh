@echo off
cd /d %~dp0
if not exist .env (copy .env.example .env & echo 已生成 .env，请先填写 ANTHROPIC_API_KEY 等再重新运行 & pause & exit /b)
if not exist .venv (python -m venv .venv)
call .venv\Scripts\activate
pip install -q -r requirements.txt
if not exist data\raw mkdir data\raw
uvicorn app.main:app --host 0.0.0.0 --port 8080
