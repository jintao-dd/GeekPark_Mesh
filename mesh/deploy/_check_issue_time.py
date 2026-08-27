import sqlite3
c = sqlite3.connect("/srv/mesh/data/mesh.db")
c.row_factory = sqlite3.Row
r = c.execute("SELECT id,slug FROM issues WHERE slug=?", ("2026-10-02",)).fetchone()
print("issue", r["id"] if r else None)
if not r:
    raise SystemExit
srcs = list(c.execute(
    "SELECT id,stype,team,title,length(text) n,extracted FROM sources WHERE issue_id=? ORDER BY id",
    (r["id"],),
))
print("sources", len(srcs))
total_chars = 0
for s in srcs:
    n = s["n"] or 0
    total_chars += n
    t = (s["title"] or "")[:60]
    print(f"  id={s['id']} {s['stype']} chars={n} extracted={s['extracted']} {t}")
items = c.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (r["id"],)).fetchone()["c"]
cards = c.execute("SELECT COUNT(*) c FROM cards WHERE issue_id=?", (r["id"],)).fetchone()["c"]
teams = list(c.execute(
    "SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team<>''",
    (r["id"],),
))
print("items", items, "cards", cards, "teams", [x["owner_team"] for x in teams])
print("total_chars", total_chars)
