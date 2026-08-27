"""把草稿里误伤禁用词的「否定过去」改成「打破旧认知」。"""
import json
import sqlite3
from datetime import datetime

con = sqlite3.connect("/srv/mesh/data/mesh.db")
row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", ("2026-08-14",)).fetchone()
if not row:
    raise SystemExit("issue not found")
iid, raw = row
if "否定" not in (raw or ""):
    print("no 否定 in draft, skip")
else:
    fixed = raw.replace("否定过去", "打破旧认知")
    if fixed == raw:
        # 兜底：只替换独立「否定」若仍在
        print("pattern 否定过去 not found; contexts:")
        i = raw.find("否定")
        print(repr(raw[max(0, i - 40) : i + 40]))
        raise SystemExit(1)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute("UPDATE issues SET draft_json=?, updated_at=? WHERE id=?", (fixed, now, iid))
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (iid, "system", "draft_json:fix_forbidden", "否定过去", "打破旧认知"),
    )
    con.commit()
    print("fixed ok; remaining 否定 count:", fixed.count("否定"))
con.close()
