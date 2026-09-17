#!/usr/bin/env python3
from __future__ import annotations

import json
import sys

from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-15"
title_kw = sys.argv[2] if len(sys.argv) > 2 else "Seeed"

con = db.connect()
row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if not row or not row["draft_json"]:
    print("NO_DRAFT", slug)
    sys.exit(1)
issue_id = row["id"]
draft = json.loads(row["draft_json"])
rels = draft.get("relations") or []

for r in rels:
    title = (r.get("title") or "")
    if title_kw.lower() not in title.lower():
        continue
    print("=== RELATION ===")
    print("title:", title)
    print("label:", r.get("label"))
    print("relation_type:", r.get("relation_type"))
    print("teams:", r.get("teams"))
    print("body:", r.get("body"))
    print("details:", r.get("details"))
    print("\n--- evidence ---")
    for e in r.get("evidence") or []:
        item_id = e.get("item_id")
        print("item_id:", item_id, "team:", e.get("team"), "source_label:", e.get("source_label"))
        print("snippet:", e.get("snippet") or e.get("quote"))
        # fetch source + item text
        if item_id:
            it = con.execute("SELECT text, source_id, owner_team FROM items WHERE id=?", (item_id,)).fetchone()
            if it:
                print("item.text:", (it["text"] or "")[:400])
                src = con.execute("SELECT title, channel, stype, meta FROM sources WHERE id=?", (it["source_id"],)).fetchone()
                if src:
                    print("source.title:", src["title"])
                    print("source.channel:", src["channel"])
                    print("source.stype:", src["stype"])
                    meta = json.loads(src["meta"] or "{}")
                    print("source.url:", meta.get("url") or meta.get("link") or "")
                    print("source.meta:", json.dumps(meta, ensure_ascii=False)[:500])
        print()
    print("=== END ===\n")
