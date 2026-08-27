import sqlite3
from app import preview_job, pipeline

slug = "2026-10-02"
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
r = con.execute(
    "SELECT id, status, length(draft_json) draft_len FROM issues WHERE slug=?",
    (slug,),
).fetchone()
print("issue", dict(r) if r else None)
if r:
    cards = con.execute(
        "SELECT team, length(card_json) n FROM cards WHERE issue_id=? ORDER BY team",
        (r["id"],),
    ).fetchall()
    print("cards", len(cards))
    for c in cards:
        print(" ", c["team"], c["n"])
con.close()
print("preview", preview_job.get_state(slug))
print("pipeline", pipeline.get_state(slug))
