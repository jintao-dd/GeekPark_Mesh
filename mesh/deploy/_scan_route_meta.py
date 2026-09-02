#!/usr/bin/env python3
"""Scan draft/published for investment-team route meta copy."""
import json
import re
import sys
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
PAT = re.compile(r"投资团队.{0,8}(可承接|可用|用得上|可对接)|可承接|用得上|可对接")

con = db.connect()
r = con.execute(
    "SELECT draft_json, published_json, status FROM issues WHERE slug=?", (slug,)
).fetchone()
con.close()
if not r:
    raise SystemExit("missing")

hits = []
for kind, raw in (("draft", r["draft_json"]), ("published", r["published_json"])):
    if not raw:
        continue
    d = json.loads(raw) if isinstance(raw, str) else raw
    for i, rel in enumerate(d.get("relations") or []):
        if not isinstance(rel, dict):
            continue
        blob_parts = [
            ("title", rel.get("title") or ""),
            ("body", rel.get("body") or ""),
            ("label", rel.get("label") or ""),
        ]
        for j, x in enumerate(rel.get("details") or []):
            blob_parts.append((f"details[{j}]", str(x)))
        for j, x in enumerate(rel.get("sources") or []):
            blob_parts.append((f"sources[{j}]", str(x)))
        for field, text in blob_parts:
            if PAT.search(text):
                hits.append({"kind": kind, "i": i, "field": field, "text": text})

print(json.dumps({"status": r["status"], "n_hits": len(hits), "hits": hits}, ensure_ascii=False, indent=2))
