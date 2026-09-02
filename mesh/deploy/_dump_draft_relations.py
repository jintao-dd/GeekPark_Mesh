import json
import sys
sys.path.insert(0, "/srv/mesh")
from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
c = db.connect()
r = c.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
c.close()
d = json.loads(r["draft_json"] or "{}")
print(json.dumps({"relations": d.get("relations"), "audit": d.get("_relation_decision_audit")}, ensure_ascii=False, indent=2))
