#!/usr/bin/env python3
"""Check AI activity: in-memory jobs (same process only) + DB extraction state + recent log hints."""
import sqlite3
from pathlib import Path

DB = Path("/srv/mesh/data/mesh.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

print("=== 各期素材抽取进度（推断挖掘是否在跑）===")
rows = con.execute(
    """
    SELECT i.slug, i.period_label, i.status,
           COUNT(s.id) AS total,
           SUM(CASE WHEN s.extracted=1 THEN 1 ELSE 0 END) AS extracted,
           (SELECT COUNT(*) FROM items WHERE issue_id=i.id) AS items
    FROM issues i
    LEFT JOIN sources s ON s.issue_id = i.id AND length(COALESCE(s.text,''))>0
    GROUP BY i.id
    ORDER BY i.date_end DESC
    LIMIT 6
    """
).fetchall()
for r in rows:
    total = r["total"] or 0
    extracted = r["extracted"] or 0
    flag = ""
    if total and 0 < extracted < total:
        flag = " ← 可能正在挖掘（部分已抽）"
    elif total and extracted == 0:
        flag = " ← 尚未开始抽取"
    elif total and extracted == total:
        flag = " ← 抽取已完成"
    print(f"{r['slug']} · {r['period_label']} · {r['status']} · 素材 {extracted}/{total} · 条目 {r['items']}{flag}")

print("\n=== 最近编辑/操作（看谁动过）===")
for e in con.execute(
    """
    SELECT e.at, e.user, e.target, i.slug
    FROM edits e
    JOIN issues i ON i.id = e.issue_id
    ORDER BY e.id DESC LIMIT 8
    """
):
    print(f"{e['at']} · {e['user']} · {e['slug']} · {e['target'][:60]}")

con.close()
