"""Search raw source text for keywords (production forensics)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
sid = int(sys.argv[2]) if len(sys.argv) > 2 else 23
keywords = sys.argv[3:] if len(sys.argv) > 3 else [
    "詹杨帆", "面壁", "硅谷 BD", "硅谷BD", "终端", "汽车算力", "端侧模型",
]

con = db.connect()
row = con.execute("SELECT s.id, s.team, s.title, s.stype, length(s.text) AS n, s.text FROM sources s JOIN issues i ON i.id=s.issue_id WHERE i.slug=? AND s.id=?", (slug, sid)).fetchone()
if not row:
    print("source not found")
    sys.exit(1)
text = row["text"] or ""
print(f"source {row['id']} team={row['team']} stype={row['stype']} len={row['n']}")
print(f"title: {row['title'][:120]}")
print()
for kw in keywords:
    idx = 0
    hits = 0
    while True:
        pos = text.find(kw, idx)
        if pos < 0:
            break
        hits += 1
        print(f"=== '{kw}' @ {pos} (hit {hits}) ===")
        print(text[max(0, pos - 250): pos + 350])
        print()
        idx = pos + len(kw)
    if hits == 0:
        print(f"'{kw}': NOT FOUND")
