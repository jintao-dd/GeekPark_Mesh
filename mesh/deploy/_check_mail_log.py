#!/usr/bin/env python3
import json
import sys
from app import db, edm

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
con = db.connect()
r = con.execute("SELECT id, slug, status FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    raise SystemExit("missing")
st = edm.mail_status_label(con, r["id"])
rows = [
    dict(x)
    for x in con.execute(
        "SELECT subject, ok, error, at, to_addr FROM mail_log WHERE issue_id=? ORDER BY id DESC LIMIT 12",
        (r["id"],),
    )
]
con.close()
print(json.dumps({
    "slug": r["slug"],
    "status": r["status"],
    "mail_label": st.get("label"),
    "mail_kind": st.get("kind"),
    "recent": rows,
}, ensure_ascii=False, indent=2, default=str))
