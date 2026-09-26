#!/usr/bin/env python3
"""Dump published item ids/titles for Retrieval Gold authoring."""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/srv/mesh")
from app import db

SLUG = (sys.argv[1] if len(sys.argv) > 1 else "2026-8-17").strip()
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 40

con = db.connect()
row = con.execute(
    "SELECT id, status FROM issues WHERE slug=?", (SLUG,)
).fetchone()
print("issue", dict(row) if row else None)
if not row:
    raise SystemExit(1)
iid = row["id"]
items = list(
    con.execute(
        "SELECT id, owner_team, team, stype, zone, kind, substr(text,1,120) AS t "
        "FROM items WHERE issue_id=? AND coalesce(blocked,0)=0 "
        "AND (merged_into IS NULL OR merged_into=0) "
        "ORDER BY id LIMIT ?",
        (iid, LIMIT),
    )
)
print("items", len(items))
for it in items:
    print(
        json.dumps(
            {
                "id": it["id"],
                "owner": it["owner_team"] or it["team"],
                "stype": it["stype"],
                "kind": it["kind"],
                "text": (it["t"] or "").replace("\n", " "),
            },
            ensure_ascii=False,
        )
    )
# also search keywords from published
pub = con.execute(
    "SELECT published_json FROM issues WHERE id=?", (iid,)
).fetchone()["published_json"]
try:
    data = json.loads(pub or "{}")
except Exception:
    data = {}
names = []
for g in (data.get("keywords") or {}).get("groups") or []:
    for it in g.get("items") or []:
        n = (it.get("name") or "").strip()
        if n:
            names.append(n)
for r in (data.get("relations") or [])[:15]:
    if isinstance(r, dict) and r.get("title"):
        names.append("REL:" + r["title"])
print("pub_names", names[:30])
con.close()
