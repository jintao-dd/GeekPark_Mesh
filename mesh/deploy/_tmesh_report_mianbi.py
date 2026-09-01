import app.db as d

c = d.connect()
slug = "2026-8-17"

print("=== 面壁/詹杨帆 items ===")
for r in c.execute(
    """SELECT id, owner_team, owner_provenance, llm_owner_team_hint, blocked, source_label, pointer
       FROM items WHERE issue_id=(SELECT id FROM issues WHERE slug=?)
       AND (text LIKE %s OR text LIKE %s OR pointer LIKE %s OR pointer LIKE %s)
       ORDER BY id""",
    (slug, "%面壁%", "%詹杨帆%", "%面壁%", "%詹杨%"),
):
    print(dict(r))

print("\n=== provenance ===")
for r in c.execute(
    """SELECT owner_provenance, COUNT(*) c FROM items
       WHERE issue_id=(SELECT id FROM issues WHERE slug=?) GROUP BY owner_provenance ORDER BY c DESC""",
    (slug,),
):
    print(dict(r))

print("\n=== draft relations (面壁) ===")
import json
row = c.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if row and row["draft_json"]:
    djson = json.loads(row["draft_json"])
    for rel in djson.get("relations") or []:
        if "面壁" in (rel.get("title") or ""):
            print(rel.get("title"), "teams=", rel.get("teams"), "weak=", rel.get("weak"))
            for ev in rel.get("evidence") or []:
                print("  ev", ev.get("item_id"), ev.get("team"), (ev.get("snippet") or "")[:60])
