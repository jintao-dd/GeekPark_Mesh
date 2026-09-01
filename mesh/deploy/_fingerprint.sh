#!/bin/bash
echo "=== PROD AFTER TMESH E2E ==="
docker exec geekpark-mesh-pg psql -U mesh -d mesh -t -A -c "SELECT slug,status,published_at FROM issues WHERE slug='2026-8-17';"
docker exec geekpark-mesh python -c "import json,app.db as d;c=d.connect();r=c.execute(\"SELECT published_json FROM issues WHERE slug='2026-8-17'\").fetchone();print('prod_relations',len(json.loads(r['published_json'] or '{}').get('relations') or []))"
echo "=== TMESH PUBLISHED ==="
docker exec geekpark-tmesh python -c "import json,app.db as d;c=d.connect();r=c.execute(\"SELECT status,published_at,published_json FROM issues WHERE slug='2026-8-17'\").fetchone();d=json.loads(r['published_json'] or '{}');print('status',r['status'],'published_at',r['published_at'],'relations',len(d.get('relations') or []),'with_evidence',sum(1 for x in d.get('relations') or [] if x.get('evidence')))"
