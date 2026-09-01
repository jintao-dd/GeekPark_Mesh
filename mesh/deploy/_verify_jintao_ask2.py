"""Verify Ask answer claims for 关于锦涛的内容."""
from app import db, search

con = db.connect()

print("=== ITEMS mentioning 锦涛 (all issues) ===")
rows = con.execute(
    """
    SELECT iss.slug, i.owner_team, i.source_label, i.text
    FROM items i JOIN issues iss ON iss.id = i.issue_id
    WHERE i.blocked=0 AND i.text LIKE '%锦涛%'
    ORDER BY iss.slug, i.id
    """
).fetchall()
for r in rows:
    print(f"[{r['slug']}] {r['owner_team']} | {r['source_label']}")
    print(" ", r["text"][:220])
    print()

print("=== 千问 + PR / 接触 (issue 2026-8-17) ===")
rows2 = con.execute(
    """
    SELECT id, owner_team, source_label, text
    FROM items WHERE issue_id=6 AND blocked=0
      AND (text LIKE '%千问%' OR text LIKE '%阿里%')
    ORDER BY id
    """
).fetchall()
for r in rows2:
    print(f"#{r['id']} {r['owner_team']} | {r['source_label']}")
    print(" ", r["text"][:240])
    print()

print("=== SEARCH recall count (模拟 Ask 检索) ===")
try:
    hits = search.search_items(con, "关于锦涛的内容", date_from="2026-06-02", limit=50)
    print(f"n_hits={len(hits)}")
    teams = {}
    for h in hits[:30]:
        t = h.get("owner_team") or h.get("team") or "?"
        teams[t] = teams.get(t, 0) + 1
    print("teams:", teams)
except Exception as e:
    print("search_err:", e)

con.close()
