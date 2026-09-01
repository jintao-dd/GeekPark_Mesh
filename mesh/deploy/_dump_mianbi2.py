import json
from app import db
con = db.connect()
issue = con.execute("SELECT id, draft_json FROM issues WHERE slug='2026-8-17'").fetchone()
draft = json.loads(issue["draft_json"] or "{}")
for r in draft.get("relations", []):
    if "面壁" in (r.get("title") or "") or "詹" in (r.get("title") or ""):
        print("DRAFT REL:", json.dumps(r, ensure_ascii=False, indent=2))

for iid in (686,687,688,673,674,675,676,677,678):
    row = con.execute(
        "SELECT id, source_id, owner_team, blocked, pointer, source_label, entities, text FROM items WHERE id=?",
        (iid,),
    ).fetchone()
    if row:
        print("\nITEM", iid, ":", json.dumps(dict(row), ensure_ascii=False))

s23 = con.execute("SELECT id, title, team, stype FROM sources WHERE id=23").fetchone()
print("\nSOURCE 23:", dict(s23))
con.close()
