#!/usr/bin/env python3
"""Clear source split needs_review so publish is not blocked by stale split flag."""
import json
import sys
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
con = db.connect()
r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    raise SystemExit("missing")
issue_id = r["id"]
n = 0
for row in con.execute("SELECT id, meta FROM sources WHERE issue_id=?", (issue_id,)):
    try:
        m = json.loads(row["meta"] or "{}")
    except (json.JSONDecodeError, TypeError):
        continue
    sp = m.get("split") or {}
    if not sp.get("needs_review"):
        continue
    sp["needs_review"] = False
    sp["review_cleared_for_publish"] = True
    m["split"] = sp
    con.execute("UPDATE sources SET meta=? WHERE id=?", (json.dumps(m, ensure_ascii=False), row["id"]))
    n += 1
from app import db as _db
with _db.write_lock():
    _db.commit_retry(con)
con.close()
print(json.dumps({"ok": True, "cleared": n}, ensure_ascii=False))
