import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
r = c.execute(
    "SELECT substr(text,25000,25000) FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1"
).fetchone()
text = r[0] if r else ""
for i, line in enumerate(text.splitlines()[:100]):
    print(f"{i:3d}|{line[:120]}")
