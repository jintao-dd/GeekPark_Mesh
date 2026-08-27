#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

slug = "2026-09-25"
con = sqlite3.connect(Path("/srv/mesh/data/mesh.db"))
con.row_factory = sqlite3.Row
i = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
if not i:
    print("issue not found")
    raise SystemExit(1)
print(f"=== {slug} · {i['period_label']} ===")
draft = i["draft_json"] or ""
print(f"draft_len={len(draft)} has_draft={bool(draft.strip() and draft.strip() not in ('{}','null'))}")
try:
    d = json.loads(draft) if draft else {}
    print(f"draft_stale={bool(d.get('_stale'))}")
except Exception as e:
    print(f"draft_parse_err={e}")
cards = con.execute("SELECT team, status FROM cards WHERE issue_id=? ORDER BY team", (i["id"],)).fetchall()
print(f"cards={len(cards)} pending={sum(1 for c in cards if c['status']=='pending')}")
for c in cards:
    print(f"  {c['team']}: {c['status']}")
edits = con.execute(
    "SELECT at, user, target FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 6",
    (i["id"],),
).fetchall()
print("recent edits:")
for e in edits:
    print(f"  {e['at']} · {e['user']} · {e['target']}")
con.close()
