#!/usr/bin/env python3
"""Publish an issue as owner via send_for_issue path equivalent."""
import json
import sys
from app import db, main

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
by = sys.argv[2] if len(sys.argv) > 2 else "owner-cli"

con = db.connect()
r = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
if not r:
    raise SystemExit("missing issue")
if not r["draft_json"]:
    raise SystemExit("no draft")
blockers = main.publish_blockers(con, r["id"], r["draft_json"] or "")
if blockers:
    print(json.dumps({"ok": False, "blockers": blockers}, ensure_ascii=False, indent=2))
    raise SystemExit(2)

data = json.loads(r["draft_json"])
data.pop("_stale", None)
import datetime
import os
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
pub_day = datetime.date.today()
pub_label = main.format_display_date(pub_day)
pub_iso = pub_day.isoformat()
payload = json.dumps(data, ensure_ascii=False)
con.execute(
    "UPDATE issues SET published_json=?, draft_json=?, status='published', published_at=?, "
    "updated_at=?, period_label=?, date_end=?, date_start=? WHERE id=?",
    (payload, payload, now, now, pub_label, pub_iso, pub_iso, r["id"]),
)
db.register_entities(con, data, slug)
db.snapshot_published_items(con, r["id"])
db.reindex_issue(con, r["id"])
main._ASK_CACHE.clear()
seq = con.execute("SELECT COUNT(*) c FROM versions WHERE issue_id=?", (r["id"],)).fetchone()["c"] + 1
cards_out = len(data.get("relations", [])) + sum(
    len(g.get("items", [])) for c in data.get("contacts", []) for g in c.get("groups", [])
)
edited = con.execute(
    "SELECT COUNT(*) c FROM edits WHERE issue_id=? AND target LIKE '%%.%%'", (r["id"],)
).fetchone()["c"]
con.execute(
    "INSERT INTO versions(issue_id,version,at,by_user,cards_out,edited_count,url,snapshot_json) VALUES(?,?,?,?,?,?,?,?)",
    (
        r["id"],
        f"v{seq}",
        now,
        by,
        cards_out,
        edited,
        f"{os.environ.get('MESH_BASE_URL','').rstrip('/')}/{slug}",
        payload,
    ),
)
con.execute(
    "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
    (r["id"], by, "publish", "", f"v{seq}"),
)
with db.write_lock():
    db.commit_retry(con)
con.close()

from app import edm_job
edm_job.enqueue_auto_send(slug, by=by)
print(json.dumps({"ok": True, "slug": slug, "published_at": now, "version": f"v{seq}"}, ensure_ascii=False))
