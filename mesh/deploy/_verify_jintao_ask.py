"""Spot-check Ask claims about 锦涛 against tmesh items."""
import json
import re
from app import db

KEYWORDS = [
    "锦涛",
    "Web Coding",
    "李源",
    "千问",
    "Agent",
    "智能体",
    "design studio",
    "设计工作室",
]

con = db.connect()
rows = con.execute(
    """
    SELECT i.id, i.issue_id, iss.slug, i.owner_team, i.source_label, i.zone, i.level,
           substr(i.text, 1, 400) AS text
    FROM items i
    JOIN issues iss ON iss.id = i.issue_id
    WHERE i.blocked = 0
      AND (
        i.text LIKE '%锦涛%'
        OR i.entities::text LIKE '%锦涛%'
        OR i.text LIKE '%Web Coding%'
        OR (i.text LIKE '%李源%' AND i.text LIKE '%账号%')
        OR (i.text LIKE '%千问%' AND i.text LIKE '%Agent%')
        OR i.text LIKE '%design studio%'
        OR i.text LIKE '%设计工作室%'
      )
    ORDER BY iss.slug DESC, i.id
    """
).fetchall()
con.close()

print(f"matched_items={len(rows)}")
for r in rows:
    print("---")
    print(dict(r))

# published issue snapshot check
con = db.connect()
pub = con.execute(
    "SELECT slug, status, length(published_json) FROM issues WHERE slug='2026-8-17'"
).fetchone()
draft = con.execute(
    "SELECT length(draft_json) FROM issues WHERE slug='2026-8-17'"
).fetchone()
con.close()
print("\nissue_state:", dict(pub) if pub else None)
print("draft_len:", draft["length(draft_json)"] if draft else None)
