import re, sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
t = c.execute("SELECT text FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1").fetchone()[0]
for i, line in enumerate(t.splitlines()):
    if re.match(r"^[\u4e00-\u9fffA-Za-z ].{0,30}数据：$", line.strip()):
        print(i, line.strip())
