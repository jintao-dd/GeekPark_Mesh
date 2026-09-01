import json
import app.db as d

c = d.connect()
r = c.execute(
    "SELECT updated_at, draft_json, published_json FROM issues WHERE slug='2026-8-17'"
).fetchone()
dj = json.loads(r["draft_json"] or "{}")
pj = json.loads(r["published_json"] or "{}")
print("updated_at", r["updated_at"])
print("draft_relations", len(dj.get("relations") or []))
print("published_relations", len(pj.get("relations") or []))
print("draft_first", (dj.get("relations") or [{}])[0].get("title") if dj.get("relations") else None)
print("published_first", (pj.get("relations") or [{}])[0].get("title") if pj.get("relations") else None)
