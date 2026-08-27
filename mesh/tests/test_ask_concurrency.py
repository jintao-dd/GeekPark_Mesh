"""Ask 并发与 SQLite 写入重试。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db


def test_commit_retry_succeeds():
    with tempfile.TemporaryDirectory() as td:
        old = db.DB_PATH
        db.DB_PATH = str(Path(td) / "t.db")
        try:
            con = db.connect()
            db.migrate(con)
            con.execute("CREATE TABLE IF NOT EXISTS t(x INTEGER)")
            con.execute("INSERT INTO t(x) VALUES (1)")
            db.commit_retry(con)
            n = con.execute("SELECT COUNT(*) c FROM t").fetchone()["c"]
            con.close()
            assert n == 1
        finally:
            db.DB_PATH = old


def test_llm_slot_releases():
    from app import ask_concurrency

    old_c, old_t = ask_concurrency._LLM_CONCURRENCY, ask_concurrency._LLM_QUEUE_TIMEOUT
    ask_concurrency._LLM_CONCURRENCY = 1
    ask_concurrency._LLM_QUEUE_TIMEOUT = 1
    ask_concurrency._LLM_SEM = __import__("threading").Semaphore(1)
    try:
        with ask_concurrency.llm_slot():
            pass
        with ask_concurrency.llm_slot():
            pass
    finally:
        ask_concurrency._LLM_CONCURRENCY = old_c
        ask_concurrency._LLM_QUEUE_TIMEOUT = old_t
        ask_concurrency._LLM_SEM = __import__("threading").Semaphore(old_c)


if __name__ == "__main__":
    test_commit_retry_succeeds()
    test_llm_slot_releases()
    print("ok")
