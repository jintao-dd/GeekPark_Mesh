import sqlite3
con = sqlite3.connect("/srv/mesh/data/mesh.db")
con.row_factory = sqlite3.Row
print("USERS:")
for u in con.execute("SELECT username, role FROM users ORDER BY role, username"):
    print(dict(u))
print("ISSUES:")
for i in con.execute(
    "SELECT slug, status, published_at, length(coalesce(draft_json,'')) d, length(coalesce(published_json,'')) p FROM issues ORDER BY id DESC LIMIT 5"
):
    print(dict(i))
