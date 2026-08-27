#!/usr/bin/env python3
import sqlite3
import json
from pathlib import Path

DB = Path("/srv/mesh/data/mesh.db")
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

slug = "2026-09-04"
issue = con.execute("SELECT id, slug, period_label, status FROM issues WHERE slug=?", (slug,)).fetchone()
if not issue:
    print("issue not found")
    raise SystemExit(1)

print("issue:", dict(issue))
sources = con.execute(
    "SELECT id, title, extracted, length(COALESCE(text,'')) AS n FROM sources WHERE issue_id=? ORDER BY id",
    (issue["id"],),
).fetchall()
print(f"sources: {len(sources)} total")
extracted = sum(1 for s in sources if s["extracted"])
print(f"extracted: {extracted}/{len(sources)}")
for s in sources:
    st = "已抽取" if s["extracted"] else "未抽取"
    title = (s["title"] or "")[:48]
    print(f"  [{s['id']}] {st} · {s['n']}字 · {title}")

items = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (issue["id"],)).fetchone()["c"]
print("items in DB:", items)

# Heuristic: if pipeline mid-run, often 1..n-1 sources extracted, last ones not yet
if extracted and extracted < len(sources):
    print("LIKELY_RUNNING: extraction in progress (partial extracted)")
elif extracted == len(sources) and len(sources) > 0:
    print("LIKELY_IDLE: all sources marked extracted")
elif extracted == 0 and len(sources) > 0:
    print("LIKELY_RUNNING_OR_NOT_STARTED: none extracted yet")

con.close()
