"""Find section header before a byte offset in source 23."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db

pos = int(sys.argv[1]) if len(sys.argv) > 1 else 368230
con = db.connect()
text = con.execute("SELECT text FROM sources WHERE id=23").fetchone()["text"]
chunk = text[max(0, pos - 8000):pos]
# section markers in T13 bundle
markers = []
for line in chunk.split("\n"):
    s = line.strip()
    if not s:
        continue
    if any(k in s for k in ("编辑部", "硅谷", "Notion", "CRM", "建联", "沟通记录", "数据：", "表格 ID", "抓取时间")):
        if len(s) < 120:
            markers.append(s)
print(f"--- markers within 8k chars before @{pos} ---")
for m in markers[-25:]:
    print(m)
