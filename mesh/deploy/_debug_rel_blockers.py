import json
import app.db as d
from app.owner_guard import _teams_in_relation, team_has_entity_items

con = d.connect()
slug = "2026-8-17"
row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
draft = json.loads(row["draft_json"] or "{}")
items = [dict(x) for x in con.execute(
    "SELECT id, owner_team, blocked, entities, text FROM items WHERE issue_id=?", (row["id"],)
)]
active = [x for x in items if not int(x.get("blocked") or 0)]
print("relations", len(draft.get("relations") or []))
for rel in draft.get("relations") or []:
    title = rel.get("title") or ""
    teams = _teams_in_relation(rel)
    if len(teams) < 2:
        continue
    missing = [t for t in teams if not team_has_entity_items(active, title, t)]
    if missing:
        print("BLOCKER", title, "teams", teams, "missing", missing, "ev", len(rel.get("evidence") or []))
