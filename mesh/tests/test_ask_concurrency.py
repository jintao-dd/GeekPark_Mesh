"""Ask 并发与 SQLite 写入重试。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, db_conn


def test_commit_retry_succeeds():
    with tempfile.TemporaryDirectory() as td:
        old_path = db_conn.DB_PATH
        old_url = db_conn.MESH_DB_URL
        db_conn.DB_PATH = str(Path(td) / "t.db")
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = db_conn.DB_PATH
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
            db_conn.DB_PATH = old_path
            db_conn.MESH_DB_URL = old_url
            db.DB_PATH = old_path


def test_llm_slot_releases():
    from app import ask_concurrency
    import threading

    old_c = ask_concurrency._ASK_CONCURRENCY
    old_t = ask_concurrency._ASK_QUEUE_TIMEOUT
    ask_concurrency._ASK_CONCURRENCY = 1
    ask_concurrency._ASK_QUEUE_TIMEOUT = 1
    ask_concurrency._ASK_SEM = threading.Semaphore(1)
    ask_concurrency._LLM_SEM = ask_concurrency._ASK_SEM
    ask_concurrency._LLM_CONCURRENCY = 1
    ask_concurrency._LLM_QUEUE_TIMEOUT = 1
    try:
        with ask_concurrency.llm_slot():
            pass
        with ask_concurrency.llm_slot(pool="ask"):
            pass
    finally:
        ask_concurrency._ASK_CONCURRENCY = old_c
        ask_concurrency._ASK_QUEUE_TIMEOUT = old_t
        ask_concurrency._ASK_SEM = threading.Semaphore(old_c)
        ask_concurrency._LLM_SEM = ask_concurrency._ASK_SEM
        ask_concurrency._LLM_CONCURRENCY = old_c
        ask_concurrency._LLM_QUEUE_TIMEOUT = old_t


def test_begin_turn_commits_session():
    """会话行在 begin_turn 返回前必须落库，否则关连接后多轮追问失效。"""
    import tempfile
    from app import ask_turn, conversation
    from app.ask_scope import AskScope

    with tempfile.TemporaryDirectory() as td:
        old_path = db_conn.DB_PATH
        old_url = db_conn.MESH_DB_URL
        db_conn.DB_PATH = str(Path(td) / "t.db")
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = db_conn.DB_PATH
        con = None
        con2 = None
        try:
            con = db.connect()
            db.init_db(seed=False)
            row = con.execute("SELECT id, username, role FROM users WHERE username='admin'").fetchone()
            user = {"id": row["id"], "username": row["username"], "role": row["role"]}
            scope = AskScope(channel="web", slug="2026-1-1", user_id=user["id"], role=user["role"])
            turn = ask_turn.begin_turn(
                con, user=user, scope=scope, q="hello",
                cache_get=lambda k: None, cache_put=lambda k, v: None,
            )
            assert not turn.get("cached")
            con.close()
            con = None

            con2 = db.connect()
            sess_row = con2.execute(
                "SELECT id FROM ask_sessions WHERE scope_key=?",
                (scope.scope_key,),
            ).fetchone()
            assert sess_row is not None
            assert sess_row["id"] == turn["sess"]["id"]
        finally:
            if con is not None:
                con.close()
            if con2 is not None:
                con2.close()
            db_conn.DB_PATH = old_path
            db_conn.MESH_DB_URL = old_url
            db.DB_PATH = old_path


def test_maintenance_lock_release_only_when_held():
    db_conn._maint_advisory_held = False
    con = db.connect()
    try:
        db_conn.release_maintenance_lock(con)
        assert not db_conn._maint_advisory_held
    finally:
        con.close()


def test_insert_id_sqlite():
    with tempfile.TemporaryDirectory() as td:
        old_path = db_conn.DB_PATH
        old_url = db_conn.MESH_DB_URL
        db_conn.DB_PATH = str(Path(td) / "t.db")
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = db_conn.DB_PATH
        con = None
        try:
            con = db.connect()
            db.init_db(seed=False)
            iid = db.insert_id(
                con,
                "INSERT INTO issues(slug,period_label,status) VALUES(?,?,?)",
                ("test-slug", "测试", "draft"),
            )
            db.commit_retry(con)
            row = con.execute("SELECT id FROM issues WHERE slug=?", ("test-slug",)).fetchone()
            assert int(row["id"]) == iid
        finally:
            if con is not None:
                con.close()
            db_conn.DB_PATH = old_path
            db_conn.MESH_DB_URL = old_url
            db.DB_PATH = old_path


if __name__ == "__main__":
    test_commit_retry_succeeds()
    test_llm_slot_releases()
    test_begin_turn_commits_session()
    test_maintenance_lock_release_only_when_held()
    test_insert_id_sqlite()
    print("ok")
