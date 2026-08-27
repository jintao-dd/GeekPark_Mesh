#!/usr/bin/env bash
# 线上恢复 / 全量同步：在 mesh 目录执行  bash deploy/restart.sh
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> 前端语法与缓存版本"
python scripts/check_frontend.py

echo "==> 本地关键文件"
for f in \
  app/qa_structured.py \
  app/search.py \
  app/tokenize.py \
  app/db.py \
  app/main.py \
  app/llm.py \
  app/static/app.js \
  app/templates/base.html \
  app/providers/base.py \
  app/providers/anthropic_provider.py \
  app/providers/openai_compat_provider.py \
  app/prompts/qa.md \
  app/prompts/00_base_rules.md \
  app/prompts/issue_draft.md \
  app/prompts/extract_T10.md \
  app/prompts/extract_T11.md \
  app/prompts/extract_T12.md \
  app/prompts/extract_T13.md \
  app/prompts/team_gp.md \
  app/prompts/team_svbd.md \
  app/prompts/team_podcast.md \
  app/prompts/team_video.md \
  app/ingest.py
do
  if [ -f "$f" ]; then echo "  ok $f"; else echo "  MISSING $f"; exit 1; fi
done

echo "==> rebuild image（app 代码在镜像里，改代码必须 build；volumes 只挂 data/ 与 prompts/）"
docker compose build

echo "==> restart"
docker compose up -d
sleep 2

echo "==> container 内模块自检"
docker exec geekpark-mesh python - <<'PY'
from app import db, qa_structured, search, tokenize, llm, main
assert hasattr(db, "team_alias_map") or hasattr(db, "_team_alias_map")
assert hasattr(db, "normalize_team")
assert hasattr(qa_structured, "parse_intent")
assert hasattr(search, "fts_search")
assert hasattr(tokenize, "build_match_query")
assert hasattr(llm, "answer_question_stream")
paths = {getattr(r, "path", None) for r in main.app.routes}
for p in ("/api/ask", "/api/ask/stream", "/healthz", "/admin/reindex_search"):
    assert p in paths, p
print("container modules OK")
PY

echo "==> health"
docker logs geekpark-mesh --tail 40 || true
curl -sS -o /dev/null -w "healthz=%{http_code}\n" http://127.0.0.1:8090/healthz || true
curl -sS -o /dev/null -w "login=%{http_code}\n" http://127.0.0.1:8090/login || true

echo "==> 提醒：Nginx 流式配置需手动同步 deploy/mesh.geekpark.ai.ssl.conf 里的 location /api/ask/stream"
echo "    当前前端 askStream=false，走 /api/ask 整段回答，避免未改 Nginx 时 502"
