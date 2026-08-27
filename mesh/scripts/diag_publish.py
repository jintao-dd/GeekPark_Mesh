#!/usr/bin/env python3
"""Diagnose publish blockers for recent issues."""
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, "/srv/mesh")
from app import db, llm
from app.main import publish_blockers

con = db.connect()
issues = con.execute(
    "SELECT id, slug, period_label, status, draft_json FROM issues ORDER BY date_end DESC LIMIT 5"
).fetchall()

for r in issues:
    print("=" * 60)
    print(f"{r['slug']} · {r['period_label']} · status={r['status']}")
    draft = r["draft_json"] or ""
    print(f"draft_ready={db.draft_is_ready(draft)} draft_len={len(draft)}")
    n_src = con.execute("SELECT COUNT(*) c FROM sources WHERE issue_id=?", (r["id"],)).fetchone()["c"]
    n_items = con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (r["id"],)).fetchone()["c"]
    n_noowner = con.execute(
        "SELECT COUNT(*) c FROM items WHERE issue_id=? AND (owner_team IS NULL OR owner_team='')",
        (r["id"],),
    ).fetchone()["c"]
    cards = [dict(x) for x in con.execute("SELECT team, status FROM cards WHERE issue_id=?", (r["id"],))]
    print(f"sources={n_src} items={n_items} noowner={n_noowner} cards={len(cards)}")
    for c in cards:
        print(f"  card {c['team']}: {c['status']}")
    blockers = publish_blockers(con, r["id"], draft)
    print("blockers:", blockers or "(none)")
    if draft and db.draft_is_ready(draft):
        try:
            hits = llm.forbidden_hits(json.dumps(json.loads(draft), ensure_ascii=False))
        except Exception:
            hits = llm.forbidden_hits(draft)
        print("forbidden_hits:", hits or "(none)")

# users who can publish
print("=" * 60)
print("owners:")
for u in con.execute("SELECT username, display, role FROM users WHERE role='owner'"):
    print(dict(u))
con.close()
