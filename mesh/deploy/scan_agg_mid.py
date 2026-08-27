import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
t = c.execute("SELECT text FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1").fetchone()[0]
lines = t.splitlines()
for i in range(130, 220):
    if i < len(lines):
        print(f"{i:4d}|{lines[i][:110]}")
