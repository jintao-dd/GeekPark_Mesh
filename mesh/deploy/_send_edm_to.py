#!/usr/bin/env python3
"""Send a test EDM to a specific address. Usage: _send_edm_to.py <slug> <email>"""
import sys

sys.path.insert(0, "/srv/mesh")
from app import db, edm, main

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
to = sys.argv[2] if len(sys.argv) > 2 else ""
if not to:
    print("ERR need email")
    sys.exit(2)

con = db.connect()
r, data, src = main.load_issue_for_edm(con, slug)
if not r:
    con.close()
    print("ERR issue not found", slug)
    sys.exit(1)
addrs = edm.parse_addrs(to)
ok, err, subj = edm.send_for_issue(
    con,
    dict(r),
    data or {},
    to_addrs=addrs,
    test=True,
    base_url=main.BASE_URL,
    logo_url=__import__("os").environ.get("MESH_LOGO_URL", ""),
)
con.close()
print("source", src)
print("to", addrs)
print("subject", subj)
print("ok", ok)
if err:
    print("err", err)
    sys.exit(1)
