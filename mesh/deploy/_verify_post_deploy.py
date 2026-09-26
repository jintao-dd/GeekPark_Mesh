"""部署后数据层校验。"""
from app import db

con = db.connect()
r = con.execute("SELECT COUNT(*) c FROM items WHERE raw_snippet IS NULL").fetchone()
print("items_missing_raw_snippet:", r["c"])
r = con.execute("SELECT COUNT(*) c FROM items").fetchone()
print("items_total:", r["c"])
r = con.execute("SELECT slug FROM issues ORDER BY slug").fetchall()
print("issues:", [x["slug"] for x in r])
r = con.execute("SELECT COUNT(DISTINCT issue_slug) c FROM item_facts").fetchone()
print("item_facts_issue_count:", r["c"])
r = con.execute("SELECT issue_slug, COUNT(*) cnt FROM item_facts GROUP BY 1 ORDER BY 1").fetchall()
print("item_facts_by_issue:", [(x["issue_slug"], x["cnt"]) for x in r])
r = con.execute(
    "SELECT COUNT(*) c FROM issues WHERE slug ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}$'"
).fetchone()
print("normalized_slug_issues:", r["c"])
