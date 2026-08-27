import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
t = c.execute("SELECT text FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1").fetchone()[0]
for pat in ["例会", "工作周报", "工作进展", "Global Partnership", "商业化部门", "攻坚讨论", "选题", "飞书文件夹"]:
    hits = [(i, l[:90]) for i, l in enumerate(t.splitlines()) if pat in l]
    print(pat, len(hits))
    for x in hits[:5]:
        print(" ", x)
