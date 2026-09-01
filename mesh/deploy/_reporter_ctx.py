"""Context around reporter names in source 23."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db

con = db.connect()
text = con.execute("SELECT text FROM sources WHERE id=23").fetchone()["text"]
names = sys.argv[1:] if len(sys.argv) > 1 else ["周永亮", "徐珊", "赵维鹏", "Notion", "建联数据库", "编辑部"]
for name in names:
    idx = 0
    n = 0
    while True:
        pos = text.find(name, idx)
        if pos < 0:
            break
        n += 1
        if n <= 3:
            print(f"=== {name} @ {pos} (#{n}) ===")
            print(text[max(0, pos - 300): pos + 400])
            print()
        idx = pos + len(name)
    if n == 0:
        print(f"{name}: NOT FOUND")
    elif n > 3:
        print(f"... {name} total hits: {n}")

# show chunk around 面壁智能—詹杨帆
needle = "面壁智能—詹杨帆"
pos = text.find(needle)
if pos >= 0:
    print(f"\n=== context for '{needle}' @ {pos} ===")
    print(text[max(0, pos - 500): pos + 800])
