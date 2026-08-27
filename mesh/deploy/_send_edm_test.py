#!/usr/bin/env python3
import sqlite3
import sys

sys.path.insert(0, "/srv/mesh")
from app import main
from app import edm

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
test = int(sys.argv[2]) if len(sys.argv) > 2 else 1

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
r, data, src = main.load_issue_for_edm(con, slug)
if not r:
    print("ERR issue not found")
    sys.exit(1)
to = edm.default_to(con)
addrs = edm.parse_addrs(to)
if not addrs:
    print("ERR no default recipient")
    sys.exit(2)
ok, err, subj = edm.send_for_issue(
    con,
    dict(r),
    data or {},
    to_addrs=addrs,
    test=bool(test),
    base_url="https://mesh.geekpark.ai",
)
print("source", src)
print("to", addrs)
print("subject", subj)
print("ok", ok)
if err:
    print("err", err)
