#!/usr/bin/env python3
import time
from app import embeddings, db

t0 = time.time()
v = embeddings.embed_one("测试向量连通性")
print({
    "ok": v is not None,
    "dim": (len(v) if v is not None else None),
    "sec": round(time.time() - t0, 2),
    "last_error": embeddings.last_error(),
    "configured": embeddings.is_configured(),
})
con = db.connect()
try:
    n_chunk = con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"]
    n_emb = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
    print({"chunks": n_chunk, "embeddings": n_emb, "gap": n_chunk - n_emb})
finally:
    con.close()
