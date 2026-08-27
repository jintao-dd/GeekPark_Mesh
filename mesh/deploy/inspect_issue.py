import json
import sqlite3

slug = "2026-08-14"
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
issue = con.execute("SELECT id, slug, status, published_at, updated_at FROM issues WHERE slug=?", (slug,)).fetchone()
iid = issue["id"]
print("issue:", dict(issue))
print("items_total:", con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (iid,)).fetchone()["c"])
print("\n=== sources (newest first) ===")
for r in con.execute(
    "SELECT id, stype, team, title, filename, extracted, length(text) n, fetched_at FROM sources WHERE issue_id=? ORDER BY id DESC",
    (iid,),
):
    print(dict(r))
print("\n=== cards ===")
for r in con.execute("SELECT id, team, status, reviewed_at FROM cards WHERE issue_id=?", (iid,)):
    print(dict(r))
print("\n=== recent edits ===")
for r in con.execute(
    "SELECT at, user, target, substr(after,1,100) after FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 10",
    (iid,),
):
    print(dict(r))
