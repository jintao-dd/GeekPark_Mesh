#!/usr/bin/env python3
"""Check duplicate entity titles and route meta in relations."""
import json
import re
import sys
from collections import defaultdict
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
PAT = re.compile(r"可承接|用得上|可对接|投资团队可用|对编辑部.*可用")

con = db.connect()
row = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
con.close()
d = json.loads(row["draft_json"] or "{}")
rels = d.get("relations") or []

def entity_key(title):
    m = re.match(r"^([^（(：:\s·•]{2,24})", (title or "").strip())
    return (m.group(1).strip().lower() if m else (title or "")[:24].lower())

by_ent = defaultdict(list)
meta_hits = []
for i, r in enumerate(rels):
    t = r.get("title") or ""
    by_ent[entity_key(t)].append({"i": i, "title": t, "teams": r.get("teams"), "body": r.get("body")})
    blob = " ".join([t, r.get("body") or ""] + [str(x) for x in (r.get("details") or [])])
    if PAT.search(blob):
        meta_hits.append({"title": t, "body": r.get("body")})

dupes = {k: v for k, v in by_ent.items() if len(v) > 1 and k}
print(json.dumps({
    "n_relations": len(rels),
    "n_dup_entities": len(dupes),
    "dupes": dupes,
    "n_meta_hits": len(meta_hits),
    "meta_hits": meta_hits[:10],
}, ensure_ascii=False, indent=2))
