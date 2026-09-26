import json
import app.db as db

c = db.connect()
rows = c.execute(
    """SELECT slug, status, embedding_status, embedding_done, embedding_total, embedding_model
       FROM issues WHERE slug='2026-8-17'"""
).fetchall()
print(json.dumps([dict(r) for r in rows], ensure_ascii=False, indent=2))
