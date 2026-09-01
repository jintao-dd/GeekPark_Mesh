#!/usr/bin/env python3
"""Read-only prod fingerprint for E2E acceptance."""
import json
import os
import sys

import psycopg2

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
url = os.environ.get("PROD_DB_URL", "postgresql://mesh:mesh@geekpark-mesh-pg:5432/mesh")
con = psycopg2.connect(url)
cur = con.cursor()
cur.execute("SELECT id, slug, status, published_at FROM issues WHERE slug=%s", (slug,))
row = cur.fetchone()
if not row:
    print(json.dumps({"error": "not found", "slug": slug}))
    sys.exit(1)
issue_id = row[0]
cur.execute("SELECT COUNT(*) FROM relations WHERE issue_id=%s", (issue_id,))
# might not have relations table - try published_json
cur.execute("SELECT published_json FROM issues WHERE id=%s", (issue_id,))
pub = cur.fetchone()[0]
n_rel = 0
if pub:
    import json as j
    try:
        n_rel = len(j.loads(pub).get("relations") or [])
    except Exception:
        pass
print(json.dumps({
    "slug": row[1],
    "status": row[2],
    "published_at": str(row[3]) if row[3] else None,
    "n_published_relations": n_rel,
}, ensure_ascii=False))
con.close()
