import sqlite3
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
print("USERS:")
for r in con.execute("SELECT id, username, display, role, team, feishu_open_id FROM users ORDER BY id"):
    print(r)
