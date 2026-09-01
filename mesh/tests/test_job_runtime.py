"""job_runtime：inline vs pending 入队。"""
import os
import tempfile
import importlib


def test_spawn_marks_pending_when_not_inline():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    os.environ["MESH_JOB_INLINE"] = "0"
    from app import db_conn, db, job_store, job_runtime

    importlib.reload(db_conn)
    importlib.reload(db)
    importlib.reload(job_store)
    importlib.reload(job_runtime)
    try:
        db.init_db(seed=False)
        defaults = {"running": False, "done": False, "token": 0, "slug": "s"}
        claimed = job_store.try_claim(
            "pipeline", "s", defaults, {**defaults, "running": True},
        )
        assert claimed
        ran = {"n": 0}

        def _fn(slug, token):
            ran["n"] += 1

        job_runtime.spawn_after_claim(
            "pipeline", "s", defaults, _fn, ("s", claimed["token"]),
        )
        st = job_store.get("pipeline", "s", defaults)
        assert st.get("_executor") == "pending"
        assert ran["n"] == 0
        assert job_runtime.mark_executor("pipeline", "s", defaults, "worker:1")
        st2 = job_store.get("pipeline", "s", defaults)
        assert st2.get("_executor") == "worker:1"
        assert not job_runtime.mark_executor("pipeline", "s", defaults, "worker:2")
    finally:
        os.environ.pop("MESH_JOB_INLINE", None)
        try:
            os.unlink(path)
        except OSError:
            pass


def test_spawn_inline_starts_thread():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    os.environ["MESH_JOB_INLINE"] = "1"
    from app import db_conn, db, job_store, job_runtime
    import time

    importlib.reload(db_conn)
    importlib.reload(db)
    importlib.reload(job_store)
    importlib.reload(job_runtime)
    try:
        db.init_db(seed=False)
        defaults = {"running": False, "done": False, "token": 0, "slug": "s2"}
        claimed = job_store.try_claim(
            "pipeline", "s2", defaults, {**defaults, "running": True},
        )
        assert claimed
        done = {"ok": False}

        def _fn(slug, token):
            done["ok"] = True
            st = job_store.get("pipeline", "s2", defaults)
            st["running"] = False
            st["done"] = True
            job_store.put("pipeline", "s2", st)

        job_runtime.spawn_after_claim(
            "pipeline", "s2", defaults, _fn, ("s2", claimed["token"]),
        )
        for _ in range(50):
            if done["ok"]:
                break
            time.sleep(0.02)
        assert done["ok"]
        st = job_store.get("pipeline", "s2", defaults)
        assert st.get("_executor") == "inline"
    finally:
        os.environ.pop("MESH_JOB_INLINE", None)
        try:
            os.unlink(path)
        except OSError:
            pass
