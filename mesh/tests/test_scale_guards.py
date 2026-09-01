"""并发与正确性相关回归：zone 硬拦、job_store、拆段 review、Ask/发布一致性约定。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, job_store, zone_hard, llm
from app.aggregator import SplitResult


def test_zone_hard_blocks_financing():
    items = [{"text": "本轮融资 5000 万美元已交割", "zone": 1, "level": "L1", "blocked": 0}]
    out = zone_hard.apply_hard_blocks(items)
    assert out[0]["blocked"] == 1


def test_zone_hard_keeps_normal():
    items = [{"text": "编辑部沟通了端云协同产品路线", "zone": 1, "level": "L1", "blocked": 0}]
    out = zone_hard.apply_hard_blocks(items)
    assert out[0]["blocked"] == 0


def test_job_store_roundtrip():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    # 强制重新绑定连接路径
    import importlib
    from app import db_conn
    importlib.reload(db_conn)
    importlib.reload(db)
    importlib.reload(job_store)
    try:
        db.init_db(seed=False)
        st = {"slug": "t", "running": True, "done": False, "error": None, "token": 3, "cur": 1}
        job_store.put("pipeline", "t-slug", st)
        got = job_store.get("pipeline", "t-slug", {"slug": "t", "running": False, "done": False, "token": 0})
        assert got["token"] == 3
        assert got["running"] is True
        assert job_store.is_current("pipeline", "t-slug", 3, {"running": False, "token": 0})
        # get 必须读库：污染缓存后仍应拿到 DB 值
        with job_store._LOCK:
            job_store._CACHE[("pipeline", "t-slug")] = {"token": 99, "running": False}
        got2 = job_store.get("pipeline", "t-slug", {"token": 0, "running": False})
        assert got2["token"] == 3 and got2["running"] is True
        job_store.drop("pipeline", "t-slug")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_job_store_try_claim_exclusive():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    import importlib
    from app import db_conn
    importlib.reload(db_conn)
    importlib.reload(db)
    importlib.reload(job_store)
    try:
        db.init_db(seed=False)
        defaults = {"running": False, "done": False, "token": 0, "slug": "x"}
        a = job_store.try_claim("pipeline", "x", defaults, {**defaults, "running": True})
        assert a and a["running"] and a["token"] == 1
        b = job_store.try_claim("pipeline", "x", defaults, {**defaults, "running": True})
        assert b is None
        c = job_store.try_claim("pipeline", "x", defaults, {**defaults, "running": True}, force=True)
        assert c and c["token"] == 2
        job_store.drop("pipeline", "x")
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def test_split_needs_review_fallback():
    assert llm.split_needs_review({"mode": "fallback", "warnings": ["x"]})
    assert not llm.split_needs_review({"mode": "multi", "segments": 3})
