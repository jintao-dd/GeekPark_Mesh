#!/usr/bin/env python3
"""Deep spot-check: evidence snippet vs item.text vs source.text excerpt."""
import json
import sys
sys.path.insert(0, "/srv/mesh")
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
checks = [
    ("飞书 · WorkBuddy", 853),
    ("高通 · 面壁智能", 688),
    ("Anthropic · 字节跳动", 906),
]

c = db.connect()
pub = json.loads(c.execute("SELECT published_json FROM issues WHERE slug=?", (slug,)).fetchone()["published_json"])
rels = {r["title"]: r for r in pub.get("relations") or []}

for title, iid in checks:
    rel = rels.get(title)
    if not rel:
        print("MISSING REL", title)
        continue
    ev = next((e for e in (rel.get("evidence") or []) if e.get("item_id") == iid), None)
    if not ev:
        print("MISSING EV", title, iid)
        continue
    item = dict(c.execute("SELECT * FROM items WHERE id=?", (iid,)).fetchone())
    src = dict(c.execute("SELECT id, title, length(text) n FROM sources WHERE id=?", (item["source_id"],)).fetchone())
    print("=" * 60)
    print("REL:", title)
    print("item_id:", iid, "source_id:", item["source_id"], "team:", item.get("owner_team"))
    print("pointer item:", repr(item.get("pointer")), "| ev:", repr(ev.get("pointer")))
    print("source:", src.get("title"), "len=", src.get("n"))
    snip = ev.get("snippet") or ""
    itxt = item.get("text") or ""
    print("snippet[:100]:", snip[:100])
    print("item.text[:100]:", itxt[:100])
    print("snippet in item:", snip in itxt)
    # source contains snippet head?
    st = c.execute("SELECT substr(text,1,500) t FROM sources WHERE id=?", (item["source_id"],)).fetchone()["t"]
    head = snip[:30]
    print("snippet head in source excerpt:", head in (st or ""))

# published evidence key sanity
sample = pub["relations"][0]
print("\nPUBLISHED KEYS:", sorted(sample.keys()))
print("n_relations with evidence:", sum(1 for r in pub["relations"] if r.get("evidence")))
c.close()
