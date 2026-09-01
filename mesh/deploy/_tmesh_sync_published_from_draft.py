"""tmesh staging：将 draft_json 同步到 published_json，便于读者页看到 preview 结果。"""
import datetime
import json
import sys

from app import db

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
con = db.connect()
row = con.execute(
    "SELECT id, status, draft_json, published_json FROM issues WHERE slug=?",
    (slug,),
).fetchone()
if not row:
    print("issue_not_found", slug)
    sys.exit(1)

draft_raw = row["draft_json"] or ""
if not draft_raw.strip():
    print("no_draft")
    sys.exit(1)

draft = json.loads(draft_raw)
pub = json.loads(row["published_json"] or "{}")
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

con.execute(
    "UPDATE issues SET published_json=?, updated_at=? WHERE id=?",
    (draft_raw, now, row["id"]),
)
db.register_entities(con, draft, slug)
db.snapshot_published_items(con, row["id"])
db.reindex_issue(con, row["id"])
con.commit()

print(
    json.dumps(
        {
            "ok": True,
            "slug": slug,
            "status": row["status"],
            "draft_relations": len(draft.get("relations") or []),
            "was_published_relations": len(pub.get("relations") or []),
            "updated_at": now,
        },
        ensure_ascii=False,
    )
)
