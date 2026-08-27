import sqlite3
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
con.row_factory = sqlite3.Row
print("=== users matching 张/山 ===")
for r in con.execute(
    "SELECT id, username, display, role, team, feishu_open_id, created_at FROM users "
    "WHERE display LIKE '%张%' OR display LIKE '%山%' OR username LIKE '%zss%' "
    "OR username LIKE '%shan%' OR display LIKE '%ZSS%' ORDER BY id"
):
    print(dict(r))
print("=== all users ===")
for r in con.execute(
    "SELECT id, username, display, role, team, feishu_open_id, created_at FROM users ORDER BY id"
):
    print(dict(r))
