import sqlite3, sys
sys.path.insert(0, "/srv/mesh")
from app import llm

con = sqlite3.connect("/srv/mesh/data/mesh.db")
r = con.execute("SELECT length(draft_json), draft_json FROM issues WHERE slug=?", ("2026-08-14",)).fetchone()
print("draft_len", r[0])
print("hits", llm.forbidden_hits(r[1] or ""))
print("has_否定", "否定" in (r[1] or ""))
if "否定" in (r[1] or ""):
    i = r[1].find("否定")
    print(repr(r[1][max(0, i - 50) : i + 50]))
if "破旧" in (r[1] or ""):
    i = r[1].find("破旧")
    print("破旧 context:", repr(r[1][max(0, i - 20) : i + 80]))
if "打破旧认知" in (r[1] or ""):
    print("already has 打破旧认知")
