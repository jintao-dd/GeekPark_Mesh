import json
import app.db as d

c = d.connect()
slug = "2026-8-17"
row = c.execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
if row and row["draft_json"]:
    dj = json.loads(row["draft_json"])
    print("relations", len(dj.get("relations") or []))
    for rel in dj.get("relations") or []:
        title = rel.get("title") or ""
        if "面壁" in title or "詹杨" in title:
            print("REL", rel.get("title"), rel.get("teams"), "weak=", rel.get("weak"))
            for ev in rel.get("evidence") or []:
                print(" ", ev.get("item_id"), ev.get("team"))

print("\n硅谷BD 面壁 items:")
for r in c.execute(
    """SELECT id, owner_team, owner_provenance, blocked, source_label, pointer
       FROM items WHERE issue_id=(SELECT id FROM issues WHERE slug=?)
       AND owner_team='硅谷 BD 团队' AND (text LIKE %s OR pointer LIKE %s)""",
    (slug, "%面壁%", "%面壁%"),
):
    print(dict(r))

print("\nteams in cards:")
for r in c.execute(
    "SELECT team FROM cards WHERE issue_id=(SELECT id FROM issues WHERE slug=?)",
    (slug,),
):
    print(r["team"])
