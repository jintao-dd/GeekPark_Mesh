"""跨 worker / 多实例共享的 Agent 会话 KV（PostgreSQL / SQLite）。

session_state 的持久层：进程内内存做加速，DB 做真相源（多 uvicorn worker、
甚至多容器均一致）。无 DB（纯本地）时回退磁盘 JSON。

表 mesh_agent_kv：
  kind  TEXT  session|pending
  k     TEXT  session_key / pending_key
  payload_json TEXT
  updated_at   TEXT（UTC；用于 TTL 回收）
  PRIMARY KEY (kind, k)

写为幂等 upsert（last-write-wins）；同一会话的飞书消息通常串行到达，
不同会话是不同 key，天然并行安全。
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

log = logging.getLogger("uvicorn.error")


def _db_enabled() -> bool:
    """有 PG（或显式允许 SQLite KV）时用 DB；否则回退磁盘。"""
    if (os.environ.get("MESH_AGENT_KV_BACKEND") or "").strip().lower() == "disk":
        return False
    try:
        from .. import db_conn

        if (getattr(db_conn, "MESH_DB_URL", "") or "").strip():
            return True
    except Exception:
        pass
    # 显式允许 SQLite KV（本地多 worker 联调）
    return (os.environ.get("MESH_AGENT_KV_BACKEND") or "").strip().lower() == "db"


_TABLE_READY = False


def ensure_table(con) -> None:
    """建表检查只做一次（进程内）。

    历史实现每次 read/write 都执行 CREATE TABLE IF NOT EXISTS：
    PG 下会拿 AccessExclusiveLock，每消息多次 DDL 检查既是往返也是锁竞争。
    失败（无权限等）不置位，下次仍会重试。
    """
    global _TABLE_READY
    if _TABLE_READY:
        return
    dialect = getattr(con, "dialect", "sqlite")
    if dialect == "postgresql":
        con.execute(
            """CREATE TABLE IF NOT EXISTS mesh_agent_kv(
              kind TEXT NOT NULL,
              k TEXT NOT NULL,
              payload_json TEXT,
              updated_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
              PRIMARY KEY (kind, k)
            )"""
        )
    else:
        con.execute(
            """CREATE TABLE IF NOT EXISTS mesh_agent_kv(
              kind TEXT NOT NULL,
              k TEXT NOT NULL,
              payload_json TEXT,
              updated_at TEXT DEFAULT (datetime('now')),
              PRIMARY KEY (kind, k)
            )"""
        )
    _TABLE_READY = True


def read(kind: str, key: str) -> dict[str, Any] | None:
    if not key or not _db_enabled():
        return None
    from .. import db

    con = db.connect()
    try:
        ensure_table(con)
        row = con.execute(
            "SELECT payload_json FROM mesh_agent_kv WHERE kind=? AND k=?",
            (kind, key),
        ).fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["payload_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            return None
        return data if isinstance(data, dict) else None
    except Exception as e:
        log.info("session_kv read fail kind=%s: %s", kind, e)
        return None
    finally:
        con.close()


def write(kind: str, key: str, payload: dict[str, Any] | None) -> bool:
    """幂等 upsert；payload=None → 删除。返回是否用了 DB（供调用方决定是否再落盘）。"""
    if not key or not _db_enabled():
        return False
    from .. import db

    con = db.connect()
    try:
        ensure_table(con)
        if not payload:
            con.execute("DELETE FROM mesh_agent_kv WHERE kind=? AND k=?", (kind, key))
        else:
            body = json.dumps(payload, ensure_ascii=False)
            dialect = getattr(con, "dialect", "sqlite")
            if dialect == "postgresql":
                con.execute(
                    """INSERT INTO mesh_agent_kv(kind, k, payload_json, updated_at)
                       VALUES(?,?,?,to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
                       ON CONFLICT (kind, k) DO UPDATE SET
                         payload_json=EXCLUDED.payload_json,
                         updated_at=to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')""",
                    (kind, key, body),
                )
            else:
                con.execute(
                    """INSERT INTO mesh_agent_kv(kind, k, payload_json, updated_at)
                       VALUES(?,?,?,datetime('now'))
                       ON CONFLICT (kind, k) DO UPDATE SET
                         payload_json=excluded.payload_json,
                         updated_at=datetime('now')""",
                    (kind, key, body),
                )
        with db.write_lock():
            db.commit_retry(con)
        return True
    except Exception as e:
        log.info("session_kv write fail kind=%s: %s", kind, e)
        try:
            con.rollback()
        except Exception:
            pass
        return False
    finally:
        con.close()


def purge_expired(kind: str, ttl_sec: int) -> int:
    """按 updated_at 回收过期条目。返回删除行数（尽力而为）。"""
    if not _db_enabled() or ttl_sec <= 0:
        return 0
    from .. import db

    con = db.connect()
    try:
        ensure_table(con)
        dialect = getattr(con, "dialect", "sqlite")
        if dialect == "postgresql":
            cur = con.execute(
                """DELETE FROM mesh_agent_kv WHERE kind=%s
                   AND updated_at::timestamp < NOW() - (%s || ' seconds')::interval""",
                (kind, str(int(ttl_sec))),
            )
        else:
            cur = con.execute(
                "DELETE FROM mesh_agent_kv WHERE kind=? AND updated_at < datetime('now', ?)",
                (kind, f"-{int(ttl_sec)} seconds"),
            )
        with db.write_lock():
            db.commit_retry(con)
        return int(getattr(cur, "rowcount", 0) or 0)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        return 0
    finally:
        con.close()


def clear_all() -> None:
    """测试用：清空 KV。"""
    if not _db_enabled():
        return
    from .. import db

    con = db.connect()
    try:
        ensure_table(con)
        con.execute("DELETE FROM mesh_agent_kv")
        with db.write_lock():
            db.commit_retry(con)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        con.close()
