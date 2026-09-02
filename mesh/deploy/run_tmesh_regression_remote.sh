#!/usr/bin/env bash
# 本地同步代码到 tmesh 并跑 Preview 后验收（在开发机执行，需 SSH 到 104.250.53.182）
set -euo pipefail
HOST="${MESH_DEPLOY_HOST:-104.250.53.182}"
PORT="${MESH_DEPLOY_PORT:-22341}"
SLUG="${1:-2026-8-17}"
LOCAL_MESH="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE="/tmp/mesh_regress_sync"

echo "==> rsync app + deploy + eval to ${HOST}"
ssh -p "$PORT" -o StrictHostKeyChecking=no "root@${HOST}" "rm -rf ${REMOTE} && mkdir -p ${REMOTE}/app ${REMOTE}/deploy ${REMOTE}/eval"
scp -P "$PORT" -r "${LOCAL_MESH}/app/." "root@${HOST}:${REMOTE}/app/"
scp -P "$PORT" "${LOCAL_MESH}/deploy/scan_published_integrity.py" "${LOCAL_MESH}/deploy/run_phase_b_acceptance.py" \
  "${LOCAL_MESH}/deploy/_publish_readiness.py" "${LOCAL_MESH}/deploy/_report_relation_decisions.py" \
  "${LOCAL_MESH}/deploy/_tmesh_start_preview.py" "${LOCAL_MESH}/deploy/_tmesh_wait_job.py" \
  "${LOCAL_MESH}/deploy/run_full_regression.py" \
  "root@${HOST}:${REMOTE}/deploy/"
scp -P "$PORT" "${LOCAL_MESH}/eval/run_final_eval.py" "${LOCAL_MESH}/eval/eval_lib.py" "${LOCAL_MESH}/eval/ask_eval_v1.jsonl" \
  "root@${HOST}:${REMOTE}/eval/"

echo "==> copy into tmesh containers + migrate"
ssh -p "$PORT" -o StrictHostKeyChecking=no "root@${HOST}" bash -s <<EOF
set -euo pipefail
for c in geekpark-tmesh geekpark-tmesh-worker; do
  docker cp ${REMOTE}/app/. \$c:/srv/mesh/app/
  docker cp ${REMOTE}/deploy/. \$c:/srv/mesh/deploy/
  docker cp ${REMOTE}/eval/. \$c:/srv/mesh/eval/
done
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python - <<'PY'
import app.db as db
c = db.connect()
db.migrate(c)
c.commit()
print("migrate_ok", db.SCHEMA_VERSION)
PY
docker restart geekpark-tmesh geekpark-tmesh-worker
sleep 10

echo "==> integrity scan (published)"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/scan_published_integrity.py ${SLUG} || true

echo "==> publish readiness (decision_tier / reader_visible)"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_publish_readiness.py ${SLUG}

echo "==> start preview"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_start_preview.py ${SLUG}

echo "==> wait preview"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_wait_job.py preview ${SLUG}

echo "==> phase B acceptance"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/run_phase_b_acceptance.py --slug ${SLUG}

echo "==> relation decisions report"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_report_relation_decisions.py ${SLUG}

echo "==> Ask 25 eval (tmesh PG)"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python eval/run_final_eval.py --corpus prod

echo "==> embedding status sample"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python - <<'PY'
import json, app.db as db
c = db.connect()
rows = c.execute(
    """SELECT slug, embedding_status, embedding_done, embedding_total, embedding_model
       FROM issues WHERE slug=?""", ("${SLUG}",)
).fetchall()
print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
PY

echo "TMESH_REGRESSION_DONE"
EOF
