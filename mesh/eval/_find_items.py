#!/usr/bin/env python3
import sys
sys.path.insert(0, "/srv/mesh")
from app import db

q = (sys.argv[1] if len(sys.argv) > 1 else "锦涛").strip()
slug = (sys.argv[2] if len(sys.argv) > 2 else "").strip()
con = db.connect()
sql = (
    "SELECT i.id, iss.slug, i.owner_team, substr(i.text,1,100) t "
    "FROM items i JOIN issues iss ON iss.id=i.issue_id "
    "WHERE coalesce(i.blocked,0)=0 AND i.text LIKE ? "
)
params = [f"%{q}%"]
if slug:
    sql += " AND iss.slug=?"
    params.append(slug)
sql += " ORDER BY i.id LIMIT 20"
for r in con.execute(sql, params):
    print(r["id"], r["slug"], (r["owner_team"] or "")[:12], (r["t"] or "").replace("\n", " "))
con.close()
