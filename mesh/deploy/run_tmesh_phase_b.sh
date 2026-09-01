#!/usr/bin/env bash
# Phase B：同步 app 到 tmesh 并 rerun preview + 验收（不碰 prod）
set -euo pipefail
STAGING="${MESH_STAGING_DIR:-/opt/geekpark-tmesh}"
CONTAINERS=(geekpark-tmesh geekpark-tmesh-worker)
APP_FILES=(
  relation_candidates.py
  relation_verify.py
  relation_gate.py
  issue_verify.py
  preview_job.py
  main.py
  owner_guard.py
)
DEPLOY_FILES=(
  run_phase_b_acceptance.py
  _tmesh_start_preview.py
  _tmesh_wait_job.py
  scan_published_integrity.py
)

for f in "${APP_FILES[@]}"; do
  test -f "$STAGING/app/$f" || { echo "missing $STAGING/app/$f"; exit 1; }
done

for c in "${CONTAINERS[@]}"; do
  for f in "${APP_FILES[@]}"; do
    docker cp "$STAGING/app/$f" "$c:/srv/mesh/app/$f"
  done
  for f in "${DEPLOY_FILES[@]}"; do
    docker cp "$STAGING/deploy/$f" "$c:/srv/mesh/deploy/$f"
  done
done

docker restart geekpark-tmesh geekpark-tmesh-worker
sleep 8

echo "==> snapshot before preview"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/run_phase_b_acceptance.py --slug 2026-8-17 --out /srv/mesh/eval/reports/phase_b_before_2026-8-17.json

echo "==> start preview"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_start_preview.py

echo "==> wait preview (up to 40min)"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/_tmesh_wait_job.py preview

echo "==> phase B acceptance"
docker exec -w /srv/mesh geekpark-tmesh env PYTHONPATH=/srv/mesh python deploy/run_phase_b_acceptance.py --slug 2026-8-17

echo "PHASE_B_TMESH_OK"
