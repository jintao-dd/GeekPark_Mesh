#!/usr/bin/env python3
"""Dump publish_blockers for a slug."""
import json
import sys
from app import db, main

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
con = db.connect()
r = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    raise SystemExit("missing")
errs = main.publish_blockers(con, r["id"], r["draft_json"] or "")
con.close()
print(json.dumps({"n": len(errs), "blockers": errs}, ensure_ascii=False, indent=2))
