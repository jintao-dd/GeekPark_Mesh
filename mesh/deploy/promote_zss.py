import sqlite3
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
con.execute(
    "UPDATE users SET role='owner' WHERE feishu_open_id=? OR display=?",
    ("ou_0e89e4e73e19850f34fa47fb815d26d6", "张山山"),
)
con.commit()
rows = con.execute(
    "SELECT username, display, role, feishu_open_id FROM users "
    "WHERE display='张山山' OR feishu_open_id='ou_0e89e4e73e19850f34fa47fb815d26d6'"
).fetchall()
for r in rows:
    print(r)
