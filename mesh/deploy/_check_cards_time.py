import sqlite3
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
slug = "2026-10-02"
r = con.execute("SELECT id, updated_at, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
print("issue updated_at", r["updated_at"], "draft", "yes" if r["draft_json"] else "no")
cards = con.execute(
    "SELECT team, reviewed_at, length(card_json) n FROM cards WHERE issue_id=? ORDER BY reviewed_at",
    (r["id"],),
).fetchall()
for c in cards:
    print(c["reviewed_at"], c["team"], c["n"])
con.close()
