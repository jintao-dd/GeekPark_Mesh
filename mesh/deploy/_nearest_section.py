"""Nearest section header before offset."""
import sys
from pathlib import Path
import re

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import db

pos = int(sys.argv[1]) if len(sys.argv) > 1 else 368230
con = db.connect()
text = con.execute("SELECT text FROM sources WHERE id=23").fetchone()["text"]
chunk = text[:pos]
# find last occurrence of known headers
patterns = [
    r"编辑部 · 沟通记录",
    r"编辑部 · 选题",
    r"硅谷 BD[^\n]*",
    r"Notion[^\n]*",
    r"建联[^\n]{0,40}",
    r"📄[^\n]+",
    r"🏢[^\n]+",
]
for pat in patterns:
    hits = list(re.finditer(pat, chunk))
    if hits:
        h = hits[-1]
        print(f"LAST {pat!r} @ {h.start()}: {h.group()[:100]}")
        print(text[max(0,h.start()-50):h.start()+200])
        print("---")
