import json
import app.db as d
from app.owner_guard import _teams_in_relation

con = d.connect()
row = con.execute("SELECT draft_json FROM issues WHERE slug='2026-8-17'").fetchone()
draft = json.loads(row["draft_json"] or "{}")
for rel in (draft.get("relations") or [])[:5]:
    print("---")
    print(rel.get("title"))
    print("teams field", rel.get("teams"))
    print("_teams_in_relation", _teams_in_relation(rel))
    print("weak", rel.get("weak"), "evidence", len(rel.get("evidence") or []))
