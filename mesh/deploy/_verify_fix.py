import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db
slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
c = db.connect()
r = c.execute("SELECT published_json FROM issues WHERE slug=?", (slug,)).fetchone()
d = json.loads(r["published_json"])
for rel in d.get("relations", []):
    if "詹" in rel.get("title", "") or "面壁" in rel.get("title", ""):
        print(json.dumps(rel, ensure_ascii=False, indent=2))
for row in c.execute("SELECT id, blocked, owner_team FROM items WHERE id IN (686,687)").fetchall():
    print(dict(row))
