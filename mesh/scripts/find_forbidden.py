#!/usr/bin/env python3
import sys
sys.path.insert(0, "/srv/mesh")
from app import llm, db

con = db.connect()
r = con.execute("SELECT draft_json FROM issues WHERE slug=?", ("2026-09-04",)).fetchone()
d = r["draft_json"]
print("snippet:", llm.forbidden_snippet(d, "建议"))
i = 0
n = 0
while True:
    i = d.find("建议", i)
    if i < 0:
        break
    n += 1
    print(n, repr(d[max(0, i - 40) : i + 50]))
    i += 2
print("count", n)
con.close()
