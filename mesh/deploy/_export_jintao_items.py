"""Export original items backing Ask answer about 锦涛."""
import json
from app import db

con = db.connect()

# Direct 锦涛 mentions
print("=" * 60)
print("A. 直接提及「锦涛」的条目")
print("=" * 60)
rows = con.execute(
    """
    SELECT i.id, iss.slug, i.owner_team, i.team, i.stype, i.zone, i.level,
           i.source_id, i.source_label, i.text, i.entities, i.signals
    FROM items i
    JOIN issues iss ON iss.id = i.issue_id
    WHERE i.blocked = 0 AND i.text LIKE '%锦涛%'
    ORDER BY iss.slug, i.id
    """
).fetchall()
for r in rows:
    d = dict(r)
    print(json.dumps(d, ensure_ascii=False, indent=2))
    if d.get("source_id"):
        src = con.execute(
            "SELECT id, title, team, stype, substr(text,1,500) AS excerpt FROM sources WHERE id=?",
            (d["source_id"],),
        ).fetchone()
        if src:
            print("  [source]", dict(src))
    print()

# 千问 PR contact (Ask claim)
print("=" * 60)
print("B. 阿里千问（公关）智能体 — 编辑部对话条目")
print("=" * 60)
r = con.execute(
    """
    SELECT i.id, iss.slug, i.owner_team, i.source_id, i.source_label, i.text
    FROM items i JOIN issues iss ON iss.id = i.issue_id
    WHERE i.id = 669
    """
).fetchone()
if r:
    print(json.dumps(dict(r), ensure_ascii=False, indent=2))
    src = con.execute("SELECT id, title, team, stype, text FROM sources WHERE id=?", (r["source_id"],)).fetchone()
    if src:
        print("[source full text]")
        print(dict(src)["text"][:3000])

# Brand creative weekly - core items
print("=" * 60)
print("C. 品牌创意团队周会 — 相关条目 (808, 821, 807)")
print("=" * 60)
for iid in (807, 808, 821):
    r = con.execute(
        """
        SELECT i.id, iss.slug, i.owner_team, i.source_id, i.source_label, i.text
        FROM items i JOIN issues iss ON iss.id = i.issue_id WHERE i.id=?
        """,
        (iid,),
    ).fetchone()
    if r:
        print(json.dumps(dict(r), ensure_ascii=False, indent=2))
        src = con.execute("SELECT id, title, team, stype, text FROM sources WHERE id=?", (r["source_id"],)).fetchone()
        if src:
            sd = dict(src)
            print(f"[source #{sd['id']}] {sd['title']} · team={sd['team']} stype={sd['stype']}")
            print(sd["text"][:2500])
        print()

con.close()
