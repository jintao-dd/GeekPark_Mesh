import json
import app.db as d
from app.owner_guard import _teams_in_relation, team_has_entity_items, _item_matches_entity

con = d.connect()
row = con.execute("SELECT id, draft_json FROM issues WHERE slug='2026-8-17'").fetchone()
draft = json.loads(row["draft_json"] or "{}")
items = [dict(x) for x in con.execute(
    "SELECT id, owner_team, blocked, entities, text FROM items WHERE issue_id=?", (row["id"],)
)]
active = [x for x in items if not int(x.get("blocked") or 0)]

for title_key in ["AI 硬件全球定义赛道 · OpenAI 路线图", "具身智能 · 世界机器人大会", "豆包 · 字节跳动"]:
    rel = next((r for r in draft.get("relations") or [] if r.get("title") == title_key), None)
    if not rel:
        print("missing rel", title_key)
        continue
    title = rel["title"]
    teams = _teams_in_relation(rel)
    print("===", title, "===")
    for t in teams:
        ok = team_has_entity_items(active, title, t)
        print(" team", t, "has_entity", ok)
        if not ok:
            for it in active:
                if it.get("owner_team") == t:
                    em = _item_matches_entity(it, title)
                    if em or (title_key.split(" · ")[0] in (it.get("text") or "")):
                        print("   item", it["id"], "entity_match", em, "text_head", (it.get("text") or "")[:50])
