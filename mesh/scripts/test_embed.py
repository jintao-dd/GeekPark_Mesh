#!/usr/bin/env python3
"""测试 Embedding API 并可选回填 chunk 向量。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chunk_index, db, embeddings


def main():
    print("configured:", embeddings.is_configured())
    print("base_url:", embeddings._base_url())
    print("model:", embeddings.model_name())
    vec = embeddings.embed_one("极客公园 Mesh 向量检索测试")
    if not vec:
        print("FAIL:", embeddings.last_error() or "empty vector")
        sys.exit(1)
    print("OK dim=", len(vec), "head=", vec[:3])
    if len(sys.argv) > 1 and sys.argv[1] == "backfill":
        db.init_db(seed=False)
        con = db.connect()
        n = chunk_index.embed_all_missing(con)
        con.commit()
        total = con.execute("SELECT COUNT(*) c FROM chunk_embeddings").fetchone()["c"]
        print(f"backfilled {n} rows; chunk_embeddings total={total}")


if __name__ == "__main__":
    main()
