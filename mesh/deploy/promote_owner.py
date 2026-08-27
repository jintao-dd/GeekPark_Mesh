import sqlite3
con = sqlite3.connect("/opt/geekpark-mesh/data/mesh.db")
con.execute("UPDATE users SET role=?, team=NULL WHERE username=?", ("owner", "fs_6e86a35b9b"))
con.commit()
print(con.execute("SELECT username, display, role FROM users WHERE username=?", ("fs_6e86a35b9b",)).fetchone())
