#!/usr/bin/env python3
import json
import sqlite3
import sys

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    sys.exit("issue not found")
out = {"issue": dict(r), "data": json.loads(r["published_json"] or "{}")}
print(json.dumps(out, ensure_ascii=False))
