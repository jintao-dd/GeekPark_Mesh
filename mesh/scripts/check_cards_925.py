#!/usr/bin/env python3
import json
import sqlite3
from pathlib import Path

slug = "2026-09-25"
con = sqlite3.connect(Path("/srv/mesh/data/mesh.db"))
con.row_factory = sqlite3.Row
i = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
cards = con.execute(
    "SELECT team, status, length(COALESCE(card_json,'')) AS clen FROM cards WHERE issue_id=? ORDER BY team",
    (i["id"],),
).fetchall()
print("cards detail:")
for c in cards:
    print(f"  {c['team']}: status={c['status']} card_json_len={c['clen']}")
edits = con.execute(
    "SELECT at, user, target FROM edits WHERE issue_id=? ORDER BY id DESC LIMIT 10",
    (i["id"],),
).fetchall()
print("edits:")
for e in edits:
    print(f"  {e['at']} · {e['user']} · {e['target']}")
con.close()
