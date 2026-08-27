import sqlite3, os
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
print("issues:")
for r in con.execute("SELECT slug, status, updated_at FROM issues"):
    print(r)
print("sources:")
for r in con.execute("SELECT id, title, extracted, length(text) FROM sources ORDER BY id DESC LIMIT 10"):
    print(r)
print("items count:")
print(con.execute("SELECT COUNT(*) FROM items").fetchone())
print("cards count:")
print(con.execute("SELECT COUNT(*) FROM cards").fetchone())
