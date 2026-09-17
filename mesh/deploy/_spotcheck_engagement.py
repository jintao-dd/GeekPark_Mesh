#!/usr/bin/env python3
from __future__ import annotations
import json
import sys
from app import db
from app.relation_display import reader_visible
from app.relation_gate import evidence_has_real_engagement

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
con = db.connect()
row = con.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if not row or not row["draft_json"]:
    print("NO_DRAFT", slug)
    sys.exit(1)
draft = json.loads(row["draft_json"])
rels = draft.get("relations") or []
print(f"slug={slug} relations={len(rels)}")
mere = [(r.get("title") or "") for r in rels if not evidence_has_real_engagement(r)]
print(f"mere_mention_fail_count={len(mere)}")
for t in mere[:20]:
    print("  MERE", t)
short = [t for t in [(r.get("title") or "") for r in rels] if len(t) <= 8 and not any(x in t for x in ("：", ":", "·", "×", "/"))]
print(f"short_title_count={len(short)}")
for t in short[:15]:
    print("  SHORT", t)
vis = [r for r in rels if reader_visible(r)]
print(f"reader_visible={len(vis)}")
for r in vis[:10]:
    print("  VIS", (r.get("title") or "")[:60], "|", (r.get("label") or "")[:40])
