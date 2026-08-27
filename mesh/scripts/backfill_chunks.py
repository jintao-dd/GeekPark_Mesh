"""回填 chunk_index + embeddings。用法：python scripts/backfill_chunks.py"""
from app import db, chunk_index

con = db.connect()
db.migrate(con)
n = chunk_index.rebuild_all(con)
con.commit()
emb = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
chunks = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
con.close()
print(f"chunks={chunks} embeddings={emb} issues={n}")
