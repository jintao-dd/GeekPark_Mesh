"""回填 FTS（中文 toks）+ 主体×团队事实表。用法：在 mesh 目录执行 python -m deploy.reindex_facts"""
from app import db

def main():
    db.init_db(seed=False)
    con = db.connect()
    n = db.reindex_all_search(con)
    fts_n = con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"]
    fact_n = con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"]
    con.commit()
    con.close()
    print(f"reindexed issues={n} fts_rows={fts_n} facts={fact_n}")

if __name__ == "__main__":
    main()
