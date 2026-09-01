import json
import app.db as d
from app.attribution_verify import _load_items, scan_issue
from app.owner_guard import _teams_in_relation, team_has_entity_items

con = d.connect()
slug = "2026-8-17"
row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
items = _load_items(con, row["id"])
draft = json.loads(row["draft_json"] or "{}")
active = [x for x in items if not int(x.get("blocked") or 0)]
print("items", len(items), "active", len(active))

title = "AI 硬件全球定义赛道 · OpenAI 路线图"
rel = next(r for r in draft["relations"] if r["title"] == title)
teams = _teams_in_relation(rel)
for t in teams:
    print(t, team_has_entity_items(active, title, t))

scan = scan_issue(con, row["id"], row["draft_json"])
print("scan blockers", len(scan.blockers))
