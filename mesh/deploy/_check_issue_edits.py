#!/usr/bin/env python3
import json, sys
sys.path.insert(0, "/srv/mesh")
from app import db
slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
c = db.connect()
r = c.execute("SELECT id,slug,status,published_at,updated_at FROM issues WHERE slug=?", (slug,)).fetchone()
print("issue:", dict(r))
iid = r["id"]
for e in c.execute("SELECT at,user,target,substr(after,1,80) after FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 10", (iid,)):
    print("edit:", dict(e))
pub = json.loads(c.execute("SELECT published_json FROM issues WHERE id=?", (iid,)).fetchone()["published_json"])
rels = pub.get("relations") or []
print("relations:", len(rels), "with_evidence:", sum(1 for x in rels if x.get("evidence")))
if rels:
    print("sample_keys:", sorted(rels[0].keys()))
c.close()
