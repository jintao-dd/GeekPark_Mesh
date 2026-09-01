"""跨 worker 的 Ask 限流（落库滑动窗口计数）。"""
from __future__ import annotations

import os
import time

from . import db

_MAX = int(os.environ.get("MESH_ASK_RATE_PER_MIN", "40") or "40")


def _ensure(con) -> None:
    dialect = getattr(con, "dialect", "sqlite")
    if dialect == "postgresql":
        con.execute(
            """CREATE TABLE IF NOT EXISTS ask_rate_hits(
              rate_key TEXT NOT NULL,
              hit_at DOUBLE PRECISION NOT NULL
            )"""
        )
    else:
        con.execute(
            """CREATE TABLE IF NOT EXISTS ask_rate_hits(
              rate_key TEXT NOT NULL,
              hit_at REAL NOT NULL
            )"""
        )
    try:
        con.execute("CREATE INDEX IF NOT EXISTS idx_ask_rate_key ON ask_rate_hits(rate_key, hit_at)")
    except Exception:
        pass


def allow(key: str) -> bool:
    if _MAX <= 0:
        return True
    now = time.time()
    window = now - 60.0
    con = db.connect()
    try:
        _ensure(con)
        con.execute("DELETE FROM ask_rate_hits WHERE hit_at < ?", (window,))
        n = con.execute(
            "SELECT COUNT(*) c FROM ask_rate_hits WHERE rate_key=? AND hit_at >= ?",
            (key, window),
        ).fetchone()["c"]
        if int(n or 0) >= _MAX:
            with db.write_lock():
                db.commit_retry(con)
            return False
        con.execute("INSERT INTO ask_rate_hits(rate_key, hit_at) VALUES(?,?)", (key, now))
        with db.write_lock():
            db.commit_retry(con)
        return True
    except Exception:
        # 限流表异常时不阻断问答
        return True
    finally:
        con.close()
