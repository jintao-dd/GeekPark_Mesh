"""重建 prod 所有已发布 issue 的 item_facts/search_fts/chunk_index，让 raw_snippet 生效。"""
from app import db

con = db.connect()
issues = con.execute(
    "SELECT id, slug FROM issues WHERE status='published' ORDER BY id"
).fetchall()
for r in issues:
    print(f"reindex {r['slug']} ...")
    db.reindex_issue(con, r["id"], items=True, rebuild_chunks=True)
    con.commit()
print("done")
