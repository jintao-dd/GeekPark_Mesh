#!/bin/bash
set -euo pipefail

SYNC=/tmp/tmesh_sync
mkdir -p "$SYNC"

echo "=== PROD FINGERPRINT BEFORE ==="
docker exec geekpark-mesh-pg psql -U mesh -d mesh -t -A -c "SELECT slug||'|'||status||'|'||COALESCE(published_at::text,'') FROM issues WHERE slug='2026-8-17';"
docker exec geekpark-mesh python -c "import json,app.db as d;c=d.connect();r=c.execute(\"SELECT published_json FROM issues WHERE slug='2026-8-17'\").fetchone();print('prod_relations',len(json.loads(r['published_json'] or '{}').get('relations') or []))"

echo "=== SYNC FILES INTO TMESH CONTAINER ==="
for f in issue_verify.py relation_gate.py relation_verify.py relation_candidates.py; do
  docker cp "$SYNC/$f" geekpark-tmesh:/srv/mesh/app/ 2>/dev/null || true
done
docker cp "$SYNC/run_tmesh_e2e_acceptance.py" geekpark-tmesh:/srv/mesh/deploy/
docker cp "$SYNC/prod_kg_spotcheck.py" geekpark-tmesh:/srv/mesh/deploy/
docker exec geekpark-tmesh mkdir -p /srv/mesh/eval/reports
docker cp "$SYNC/run_final_eval.py" geekpark-tmesh:/srv/mesh/eval/
docker cp "$SYNC/eval_lib.py" geekpark-tmesh:/srv/mesh/eval/
docker cp "$SYNC/ask_eval_v1.jsonl" geekpark-tmesh:/srv/mesh/eval/

echo "=== RESTORE TMESH DRAFT FROM PROD (read-only prod) ==="
docker exec geekpark-mesh python -c "import app.db as d; c=d.connect(); r=c.execute(\"SELECT published_json FROM issues WHERE slug='2026-8-17'\").fetchone(); open('/tmp/prod_pub_2026.json','w',encoding='utf-8').write(r['published_json'] or '')"
docker cp geekpark-mesh:/tmp/prod_pub_2026.json /tmp/prod_pub_2026.json
docker cp /tmp/prod_pub_2026.json geekpark-tmesh:/tmp/prod_pub_2026.json
docker exec -w /srv/mesh geekpark-tmesh python -c "
import json, app.db as db
payload=open('/tmp/prod_pub_2026.json',encoding='utf-8').read()
con=db.connect()
row=con.execute(\"SELECT id FROM issues WHERE slug='2026-8-17'\").fetchone()
con.execute(\"UPDATE issues SET draft_json=?, published_json=NULL, status='draft', published_at=NULL WHERE id=?\", (payload, row['id']))
db.reindex_issue(con, row['id'])
con.commit()
d=json.loads(payload)
print(json.dumps({'restored':True,'n_relations':len(d.get('relations') or [])}, ensure_ascii=False))
"

echo "=== TMESH STATE BEFORE E2E ==="
docker exec geekpark-tmesh python -c "import json,app.db as d;c=d.connect();r=c.execute(\"SELECT slug,status FROM issues WHERE slug='2026-8-17'\").fetchone();d=json.loads(c.execute('SELECT draft_json FROM issues WHERE slug=?',('2026-8-17',)).fetchone()['draft_json']);print(dict(r),'draft_relations',len(d.get('relations') or []))"

echo "=== RUN E2E (merge+verify+owner+publish+ask) ==="
docker exec -w /srv/mesh geekpark-tmesh python deploy/run_tmesh_e2e_acceptance.py --slug 2026-8-17 --base-url http://tmesh.geekpark.net --skip-preview
EC=$?

echo "=== PROD FINGERPRINT AFTER ==="
docker exec geekpark-mesh-pg psql -U mesh -d mesh -t -A -c "SELECT slug||'|'||status||'|'||COALESCE(published_at::text,'') FROM issues WHERE slug='2026-8-17';"
docker exec geekpark-mesh python -c "import json,app.db as d;c=d.connect();r=c.execute(\"SELECT published_json FROM issues WHERE slug='2026-8-17'\").fetchone();print('prod_relations',len(json.loads(r['published_json'] or '{}').get('relations') or []))"

echo "=== REPORT ==="
docker exec geekpark-tmesh cat /srv/mesh/eval/reports/TMESH_E2E_ACCEPTANCE.md

exit $EC
