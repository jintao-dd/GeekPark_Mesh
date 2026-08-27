#!/bin/bash
set -e
docker exec -w /srv/mesh geekpark-mesh python - <<'PY'
from app import db
print("has_draft_is_ready", hasattr(db, "draft_is_ready"))
print("empty", db.draft_is_ready(""))
print("obj", db.draft_is_ready("{}"))
PY
echo "login=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/login)"
echo "admin=$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8090/admin/issue/2026-08-14)"
docker logs --tail 25 geekpark-mesh 2>&1 | tail -25
