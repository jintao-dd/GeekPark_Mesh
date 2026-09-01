"""Query guard：无效输入不进入检索。"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import ask_engine, db, db_conn
from app.ask_query_guard import guard_direct_answer, is_nonsense_query
from app.ask_scope import AskScope


def test_nonsense_detection():
    assert is_nonsense_query("xyzrandomquery999nodata")
    assert is_nonsense_query("asdfghjklqwertyuiop123456")
    assert not is_nonsense_query("面壁智能")
    assert not is_nonsense_query("Global Partnership 团队本周接触了谁")
    assert not is_nonsense_query("HeyGen")
    assert not is_nonsense_query("完全不存在的随机关键词xyz123")


def test_prepare_skips_retrieval_for_nonsense():
    with tempfile.TemporaryDirectory() as td:
        old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
        db_conn.DB_PATH = str(Path(td) / "t.db")
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = db_conn.DB_PATH
        con = db.connect()
        try:
            db.init_db(seed=False)
            scope = AskScope(channel="web", user_id=1, role="viewer")
            prepared = ask_engine.prepare(con, "xyzrandomquery999nodata", scope, history=[])
            assert prepared.get("direct_answer")
            assert prepared.get("n_hits") == 0
            assert prepared.get("n_context") == 0
            assert prepared.get("mode") == "guard"
            assert "未在已上线周报" in prepared["direct_answer"]
        finally:
            con.close()
            db_conn.DB_PATH, db_conn.MESH_DB_URL = old_path, old_url


def test_guard_message():
    assert guard_direct_answer("xyzrandomquery999nodata")
    assert guard_direct_answer("编辑部接触了面壁") is None
