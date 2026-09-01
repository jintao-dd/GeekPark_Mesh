#!/usr/bin/env bash
echo "=== md5 prod vs tmesh ==="
for c in geekpark-mesh geekpark-tmesh; do
  echo "-- $c --"
  docker exec "$c" md5sum /srv/mesh/app/attribution.py /srv/mesh/app/narrative_apply.py /srv/mesh/app/narrative_clean.py /srv/mesh/app/attribution_verify.py 2>/dev/null || true
done
echo "=== prod published ==="
docker exec geekpark-mesh-pg psql -U mesh -d mesh -t -A -c "SELECT slug||'|'||status||'|'||COALESCE(published_at::text,'') FROM issues WHERE slug='2026-8-17';"
docker exec geekpark-mesh env PYTHONPATH=/srv/mesh python - <<'PY'
import json, app.db as d
c = d.connect()
r = c.execute("SELECT published_json FROM issues WHERE slug='2026-8-17'").fetchone()
n = len(json.loads(r["published_json"] or "{}").get("relations") or [])
print("prod_published_relations", n)
PY
