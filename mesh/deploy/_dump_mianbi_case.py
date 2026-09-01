"""Dump 面壁智能 relation + items + sources from tmesh."""
import json
from app import db

con = db.connect()
issue = con.execute("SELECT id, slug, published_json, draft_json FROM issues WHERE slug='2026-8-17'").fetchone()
pub = json.loads(issue["published_json"] or "{}")
rel = next((r for r in pub.get("relations", []) if "面壁" in (r.get("title") or "")), None)
print("=== PUBLISHED RELATION (面壁) ===")
print(json.dumps(rel, ensure_ascii=False, indent=2))

items = con.execute(
    """SELECT i.id, i.source_id, i.owner_team, i.team, i.blocked, i.pointer,
              i.source_label, i.entities, i.text
       FROM items i WHERE i.issue_id=? AND (
         i.text LIKE '%面壁%' OR i.text LIKE '%詹杨帆%' OR i.entities::text LIKE '%面壁%'
       ) ORDER BY i.id""",
    (issue["id"],),
).fetchall()
print("\n=== ITEMS ===")
for r in items:
    print(json.dumps(dict(r), ensure_ascii=False, indent=2))

src_ids = sorted({r["source_id"] for r in items if r["source_id"]})
print("\n=== SOURCES ===")
for sid in src_ids:
    s = con.execute("SELECT id, title, team, stype, substr(text,1,1200) AS excerpt FROM sources WHERE id=?", (sid,)).fetchone()
    print(json.dumps(dict(s), ensure_ascii=False, indent=2))

# also show blocked fake items 686/687 if exist
print("\n=== BLOCKED 686/687 ===")
for iid in (686, 687, 688):
    r = con.execute("SELECT id, source_id, owner_team, blocked, pointer, source_label, text FROM items WHERE id=?", (iid,)).fetchone()
    if r:
        print(json.dumps(dict(r), ensure_ascii=False, indent=2))
con.close()
