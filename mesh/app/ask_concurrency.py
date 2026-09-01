"""LLM 并发控制：Ask 与后台任务分池（进程内 semaphore + DB 全站槽）。

Ask 池：slot_id 0 .. ASK_GLOBAL-1
Job 池：slot_id 1000 .. 1000+JOB_GLOBAL-1
互不挤占。
"""
from __future__ import annotations

import os
import threading
import time

_ASK_CONCURRENCY = max(1, int(os.environ.get("MESH_ASK_LLM_CONCURRENCY", "2") or "2"))
_ASK_GLOBAL = max(
    _ASK_CONCURRENCY,
    int(os.environ.get("MESH_ASK_LLM_GLOBAL", "4") or "4"),
)
_JOB_CONCURRENCY = max(1, int(os.environ.get("MESH_JOB_LLM_CONCURRENCY", "2") or "2"))
_JOB_GLOBAL = max(
    _JOB_CONCURRENCY,
    int(os.environ.get("MESH_JOB_LLM_GLOBAL", "2") or "2"),
)
_ASK_QUEUE_TIMEOUT = max(1, int(os.environ.get("MESH_ASK_QUEUE_TIMEOUT", "120") or "120"))
_JOB_QUEUE_TIMEOUT = max(1, int(os.environ.get("MESH_JOB_QUEUE_TIMEOUT", "600") or "600"))

# 兼容旧测试/引用
_LLM_CONCURRENCY = _ASK_CONCURRENCY
_LLM_GLOBAL = _ASK_GLOBAL
_LLM_QUEUE_TIMEOUT = _ASK_QUEUE_TIMEOUT

_ASK_SEM = threading.Semaphore(_ASK_CONCURRENCY)
_JOB_SEM = threading.Semaphore(_JOB_CONCURRENCY)
_LLM_SEM = _ASK_SEM  # 兼容

_JOB_SLOT_BASE = 1000


class AskBusyError(Exception):
    """排队超时：当前并发已满。"""


class JobBusyError(Exception):
    """后台 LLM 槽排队超时。"""


def _ensure_slots_table(con) -> None:
    dialect = getattr(con, "dialect", "sqlite")
    if dialect == "postgresql":
        con.execute(
            "CREATE TABLE IF NOT EXISTS mesh_llm_slots("
            "slot_id INTEGER PRIMARY KEY, holder TEXT, taken_at DOUBLE PRECISION)"
        )
    else:
        con.execute(
            "CREATE TABLE IF NOT EXISTS mesh_llm_slots("
            "slot_id INTEGER PRIMARY KEY, holder TEXT, taken_at REAL)"
        )


def _pool_params(pool: str) -> tuple[threading.Semaphore, int, int, float, type[Exception], str]:
    if pool == "job":
        return (
            _JOB_SEM,
            _JOB_SLOT_BASE,
            _JOB_GLOBAL,
            float(_JOB_QUEUE_TIMEOUT),
            JobBusyError,
            "后台任务较多，请稍后再试",
        )
    return (
        _ASK_SEM,
        0,
        _ASK_GLOBAL,
        float(_ASK_QUEUE_TIMEOUT),
        AskBusyError,
        "当前问答较多，请稍后再试",
    )


def _acquire_db_slot(timeout: float, *, base: int, n: int) -> int | None:
    from . import db, db_conn

    deadline = time.time() + timeout
    holder = f"pid:{os.getpid()}:{threading.get_ident()}"
    while time.time() < deadline:
        con = db.connect()
        try:
            _ensure_slots_table(con)
            con.execute("DELETE FROM mesh_llm_slots WHERE taken_at < ?", (time.time() - 600,))
            for i in range(base, base + n):
                try:
                    con.execute(
                        "INSERT INTO mesh_llm_slots(slot_id, holder, taken_at) VALUES(?,?,?)",
                        (i, holder, time.time()),
                    )
                    with db.write_lock():
                        db.commit_retry(con)
                    return i
                except db_conn.IntegrityError:
                    try:
                        con.rollback()
                    except Exception:
                        pass
                    continue
                except Exception:
                    try:
                        con.rollback()
                    except Exception:
                        pass
                    break
        finally:
            con.close()
        time.sleep(0.08)
    return None


def _release_db_slot(slot_id: int | None) -> None:
    if slot_id is None:
        return
    from . import db

    con = db.connect()
    try:
        con.execute("DELETE FROM mesh_llm_slots WHERE slot_id=?", (slot_id,))
        with db.write_lock():
            db.commit_retry(con)
    except Exception:
        pass
    finally:
        con.close()


class llm_slot:
    """获取 LLM 并发槽；pool=ask|job。"""

    def __init__(self, pool: str = "ask"):
        self.pool = "job" if pool == "job" else "ask"
        self._slot = None
        self._sem = None

    def __enter__(self):
        sem, base, n, timeout, exc_cls, msg = _pool_params(self.pool)
        self._sem = sem
        if not sem.acquire(timeout=timeout):
            raise exc_cls(msg)
        self._slot = _acquire_db_slot(timeout, base=base, n=n)
        if self._slot is None:
            sem.release()
            raise exc_cls(msg)
        return self

    def __exit__(self, exc_type, exc, tb):
        _release_db_slot(getattr(self, "_slot", None))
        if self._sem is not None:
            self._sem.release()
        return False
