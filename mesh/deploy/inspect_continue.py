import json, sqlite3, sys
sys.path.insert(0, "/srv/mesh")
from app import pipeline

con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
slug = "2026-08-21"
r = con.execute(
    "SELECT id, slug, status, length(coalesce(draft_json,'')) d, length(coalesce(published_json,'')) p, updated_at FROM issues WHERE slug=?",
    (slug,),
).fetchone()
print("issue", dict(r) if r else None)
if not r:
    raise SystemExit(0)
iid = r["id"]
print("sources:")
for x in con.execute(
    "SELECT id, stype, team, title, extracted, length(text) n FROM sources WHERE issue_id=? ORDER BY id",
    (iid,),
):
    print(" ", dict(x))
print("items", con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (iid,)).fetchone()["c"])
print("cards:")
for x in con.execute("SELECT team, status FROM cards WHERE issue_id=?", (iid,)):
    print(" ", dict(x))
if r["d"]:
    data = json.loads(con.execute("SELECT draft_json FROM issues WHERE id=?", (iid,)).fetchone()[0])
    print("draft relations", len(data.get("relations") or []), "contacts", len(data.get("contacts") or []), "stale", data.get("_stale"))
print("pipeline state:")
print(json.dumps(pipeline.get_state(slug), ensure_ascii=False, indent=2)[:2500])
print("recent edits:")
for e in con.execute(
    "SELECT at, user, target, substr(after,1,100) a FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 12",
    (iid,),
):
    print(" ", dict(e))
