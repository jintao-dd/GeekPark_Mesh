import sqlite3, json
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
print("sample items:")
for r in con.execute("SELECT id, owner_team, zone, level, blocked, substr(text,1,80) FROM items LIMIT 5"):
    print(r)
