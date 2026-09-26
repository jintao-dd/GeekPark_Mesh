#!/usr/bin/env python3
import json
import sys
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
con = db.connect()
r = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
con.close()
d = json.loads((r["draft_json"] if hasattr(r, "keys") else r[0]) or "{}")
for x in d.get("relations") or []:
    print(
        json.dumps(
            {
                "title": x.get("title"),
                "weak": x.get("weak"),
                "needs_review": x.get("needs_review"),
                "tier": x.get("decision_tier"),
                "teams": x.get("teams"),
                "n_ev": len(x.get("evidence") or []),
            },
            ensure_ascii=False,
        )
    )
print("VERIFY", json.dumps(d.get("_verify") or {}, ensure_ascii=False))
