#!/usr/bin/env bash
# 同步 /opt/geekpark-mesh/app 到生产容器（不 publish、不跑 pipeline）
set -euo pipefail
BASE="${MESH_DEPLOY_REMOTE_BASE:-/opt/geekpark-mesh}"
for c in geekpark-mesh geekpark-mesh-worker; do
  docker cp "$BASE/app/." "$c:/srv/mesh/app/"
  for f in run_attribution_verify.py apply_narrative_clean.py final_publish_closeout.py \
           classify_relation_blockers.py preview_narrative_clean.py; do
    docker cp "$BASE/deploy/$f" "$c:/srv/mesh/deploy/$f"
  done
done
docker exec -w /srv/mesh geekpark-mesh env PYTHONPATH=/srv/mesh python - <<'PY'
import app.db as db
c = db.connect()
db.migrate(c)
c.commit()
print("migrate_ok")
PY
docker restart geekpark-mesh geekpark-mesh-worker
sleep 6
docker exec geekpark-mesh ls /srv/mesh/app/attribution.py /srv/mesh/app/narrative_clean.py /srv/mesh/app/narrative_apply.py
docker exec geekpark-mesh env PYTHONPATH=/srv/mesh python - <<'PY'
import app.attribution as a
print("attribution_ok", a.PROVENANCE_MANUAL)
PY
docker exec geekpark-mesh-worker ls /srv/mesh/app/attribution.py
echo "SYNC_PROD_APP_OK"
