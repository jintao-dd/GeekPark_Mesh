#!/usr/bin/env python3
"""Compare current published_json vs versions snapshot for an issue."""
import json, sys
sys.path.insert(0, "/srv/mesh")
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
c = db.connect()
iss = c.execute("SELECT id, published_at, updated_at FROM issues WHERE slug=?", (slug,)).fetchone()
iid = iss["id"]
pub = json.loads(c.execute("SELECT published_json FROM issues WHERE id=?", (iid,)).fetchone()["published_json"])
vers = c.execute("SELECT version, at, snapshot_json FROM versions WHERE issue_id=? ORDER BY id", (iid,)).fetchall()
print("current published_at:", iss["published_at"])
print("versions:", [(v["version"], v["at"]) for v in vers])
if not vers:
    print("no version snapshots")
    c.close()
    sys.exit(0)
v1 = json.loads(vers[0]["snapshot_json"] or "{}")
cur_rels = pub.get("relations") or []
old_rels = v1.get("relations") or []
cur_t = [r.get("title") for r in cur_rels]
old_t = [r.get("title") for r in old_rels]
print("relations count: current", len(cur_rels), "v1", len(old_rels))
added = sorted(set(cur_t) - set(old_t))
removed = sorted(set(old_t) - set(cur_t))
if added:
    print("added titles:", added[:8], ("..." if len(added) > 8 else ""))
if removed:
    print("removed titles:", removed[:8], ("..." if len(removed) > 8 else ""))
# weak flag diff
cur_weak = [r["title"] for r in cur_rels if r.get("weak")]
old_weak = [r["title"] for r in old_rels if r.get("weak")]
print("weak current:", cur_weak)
print("weak v1:", old_weak)
print("current has evidence on all:", all(r.get("evidence") for r in cur_rels))
print("v1 has evidence:", sum(1 for r in old_rels if r.get("evidence")), "/", len(old_rels))
c.close()
