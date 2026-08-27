import sys
sys.path.insert(0, "/srv/mesh")
from app.aggregator import split_bundle_ex
import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
t = c.execute("SELECT text, title FROM sources WHERE title LIKE '%数据聚合%' ORDER BY id DESC LIMIT 1").fetchone()
text, title = t[0], t[1] or ""
r = split_bundle_ex(text, source_title=title)
print("segments", len(r.segments), "mode", r.mode)
print("meta", r.to_meta())
for s in r.segments[:12]:
    print(s.stype, s.team, len(s.text), s.title[:60])
