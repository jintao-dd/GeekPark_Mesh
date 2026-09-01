#!/usr/bin/env python3
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app.db as d
c = d.connect()
pub = json.loads(c.execute("SELECT published_json FROM issues WHERE slug='2026-8-17'").fetchone()["published_json"])
for r in pub.get("relations") or []:
    if "面壁" in (r.get("title") or ""):
        print("TITLE:", r.get("title"))
        print("LABEL:", r.get("label"))
        print("WEAK:", r.get("weak"))
        print("TEAMS:", r.get("teams"))
        print("SOURCES:", r.get("sources"))
        print("DETAILS:", json.dumps(r.get("details") or [], ensure_ascii=False)[:500])
        for e in (r.get("evidence") or [])[:6]:
            iid = e.get("item_id")
            it = c.execute("SELECT id,text,pointer,owner_team,blocked,source_label FROM items WHERE id=?", (iid,)).fetchone()
            print("--- item", iid, "team", e.get("team"), "blocked", dict(it)["blocked"] if it else None)
            print("    source_label:", dict(it).get("source_label") if it else None)
            print("    snippet:", (e.get("snippet") or "")[:100])
            print("    text_head:", (dict(it)["text"][:120] if it else ""))
        break
for iid in [686, 687, 688]:
    it = c.execute("SELECT id,blocked,owner_team,source_label,text FROM items WHERE id=?", (iid,)).fetchone()
    if it:
        d = dict(it)
        print("ITEM", iid, "blocked", d["blocked"], "team", d["owner_team"], "label", d["source_label"])
        print("  text:", d["text"][:100])
