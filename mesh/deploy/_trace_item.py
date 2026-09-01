#!/usr/bin/env python3
"""Trace items/sources for a name on an issue."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
needle = sys.argv[2] if len(sys.argv) > 2 else "詹"

con = db.connect()
issue = con.execute("SELECT id, slug, status FROM issues WHERE slug=?", (slug,)).fetchone()
if not issue:
    print("issue not found")
    sys.exit(1)
iid = issue["id"]
print(f"issue {issue['slug']} id={iid} status={issue['status']}")

rows = con.execute(
    """SELECT id, owner_team, team, stype, source_label, left(text, 200) AS snip, entities
       FROM items WHERE issue_id=? AND (text LIKE ? OR entities LIKE ?)
       ORDER BY owner_team, id""",
    (iid, f"%{needle}%", f"%{needle}%"),
).fetchall()
print(f"\nitems matching '{needle}': {len(rows)}")
for r in rows:
    print("---")
    print(dict(r))

if len(sys.argv) > 3:
    ids = [int(x) for x in sys.argv[3].split(",")]
    for iid2 in ids:
        row = con.execute(
            """SELECT i.id, i.source_id, i.owner_team, s.team AS src_team, s.title, s.stype,
                      left(s.text, 300) AS src_head
               FROM items i JOIN sources s ON s.id=i.source_id WHERE i.id=?""",
            (iid2,),
        ).fetchone()
        print(f"\n=== item {iid2} source ===")
        print(dict(row) if row else "missing")

src = con.execute(
    """SELECT s.id, s.team, s.title, s.stype, s.extracted,
              CASE WHEN length(s.text)>0 THEN left(s.text,150) ELSE '' END AS head
       FROM sources s
       JOIN items i ON i.source_id=s.id
       WHERE i.issue_id=? AND (i.text LIKE ? OR i.entities LIKE ?)
       GROUP BY s.id ORDER BY s.id""",
    (iid, f"%{needle}%", f"%{needle}%"),
).fetchall()
print(f"\nsources linked: {len(src)}")
for r in src:
    print(dict(r))

pub = con.execute("SELECT published_json FROM issues WHERE id=?", (iid,)).fetchone()
if pub and pub.get("published_json"):
    data = json.loads(pub["published_json"])
    for rel in data.get("relations") or []:
        if needle in json.dumps(rel, ensure_ascii=False):
            print("\nrelation block:")
            print(json.dumps(rel, ensure_ascii=False, indent=2)[:2000])

con.close()
