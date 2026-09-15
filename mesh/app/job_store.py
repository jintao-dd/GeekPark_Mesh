"""跨 worker 共享的后台任务状态（PostgreSQL / SQLite）。

多 uvicorn worker 下进程内 dict 会丢进度、轮询打到空 worker。
任务状态以 mesh_jobs 为准；get() 始终读库。try_claim 做原子抢占。
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

from . import db

_LOCK = threading.Lock()
# 仅作同进程写后的加速；get / is_current / try_claim 以 DB 为准
_CACHE: dict[tuple[str, str], dict] = {}

# 卡住任务回收阈值（秒）；可用 MESH_JOB_STALE_SEC 覆盖
_STALE_SEC = max(60, int(os.environ.get("MESH_JOB_STALE_SEC", "1800") or "1800"))


def ensure_table(con) -> None:
    dialect = getattr(con, "dialect", "sqlite")
    if dialect == "postgresql":
        con.execute(
            """CREATE TABLE IF NOT EXISTS mesh_jobs(
              job_kind TEXT NOT NULL,
              job_key TEXT NOT NULL,
              token INTEGER NOT NULL DEFAULT 0,
              running INTEGER NOT NULL DEFAULT 0,
              done INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              payload_json TEXT,
              updated_at TEXT DEFAULT to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'),
              PRIMARY KEY (job_kind, job_key)
            )"""
        )
    else:
        con.execute(
            """CREATE TABLE IF NOT EXISTS mesh_jobs(
              job_kind TEXT NOT NULL,
              job_key TEXT NOT NULL,
              token INTEGER NOT NULL DEFAULT 0,
              running INTEGER NOT NULL DEFAULT 0,
              done INTEGER NOT NULL DEFAULT 0,
              error TEXT,
              payload_json TEXT,
              updated_at TEXT DEFAULT (datetime('now')),
              PRIMARY KEY (job_kind, job_key)
            )"""
        )


def _row_to_state(row, defaults: dict) -> dict:
    if not row:
        return dict(defaults)
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}
    st = dict(defaults)
    st.update(payload if isinstance(payload, dict) else {})
    st["token"] = int(row["token"] or 0)
    st["running"] = bool(row["running"])
    st["done"] = bool(row["done"])
    st["error"] = row["error"]
    st["_updated_at"] = row["updated_at"] if "updated_at" in row.keys() else None
    return st


def _cache_set(kind: str, key: str, state: dict) -> None:
    with _LOCK:
        _CACHE[(kind, key)] = dict(state)


def _cache_drop(kind: str, key: str) -> None:
    with _LOCK:
        _CACHE.pop((kind, key), None)


def get(kind: str, key: str, defaults: dict) -> dict:
    """始终读库，避免跨 worker 读到陈旧进程缓存。"""
    con = db.connect()
    try:
        ensure_table(con)
        row = con.execute(
            "SELECT token, running, done, error, payload_json, updated_at "
            "FROM mesh_jobs WHERE job_kind=? AND job_key=?",
            (kind, key),
        ).fetchone()
        st = _row_to_state(row, defaults)
        _cache_set(kind, key, st)
        return st
    finally:
        con.close()


def put(kind: str, key: str, state: dict) -> None:
    payload = {
        k: v
        for k, v in state.items()
        if k not in ("running", "done", "error", "token", "_updated_at")
    }
    token = int(state.get("token") or 0)
    running = 1 if state.get("running") else 0
    done = 1 if state.get("done") else 0
    error = state.get("error")
    body = json.dumps(payload, ensure_ascii=False)
    con = db.connect()
    try:
        ensure_table(con)
        dialect = getattr(con, "dialect", "sqlite")
        if dialect == "postgresql":
            con.execute(
                """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                   VALUES(?,?,?,?,?,?,?,to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
                   ON CONFLICT (job_kind, job_key) DO UPDATE SET
                     token=EXCLUDED.token, running=EXCLUDED.running, done=EXCLUDED.done,
                     error=EXCLUDED.error, payload_json=EXCLUDED.payload_json,
                     updated_at=to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')""",
                (kind, key, token, running, done, error, body),
            )
        else:
            con.execute(
                """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                   VALUES(?,?,?,?,?,?,?,datetime('now'))
                   ON CONFLICT (job_kind, job_key) DO UPDATE SET
                     token=excluded.token, running=excluded.running, done=excluded.done,
                     error=excluded.error, payload_json=excluded.payload_json,
                     updated_at=datetime('now')""",
                (kind, key, token, running, done, error, body),
            )
        with db.write_lock():
            db.commit_retry(con)
    finally:
        con.close()
    _cache_set(kind, key, state)


def patch(kind: str, key: str, defaults: dict, **kw: Any) -> dict:
    st = get(kind, key, defaults)
    st.update(kw)
    put(kind, key, st)
    return st


def drop(kind: str, key: str) -> None:
    con = db.connect()
    try:
        ensure_table(con)
        con.execute("DELETE FROM mesh_jobs WHERE job_kind=? AND job_key=?", (kind, key))
        with db.write_lock():
            db.commit_retry(con)
    finally:
        con.close()
    _cache_drop(kind, key)


def is_current(kind: str, key: str, token: int, defaults: dict) -> bool:
    st = get(kind, key, defaults)
    return int(st.get("token") or 0) == int(token) and bool(st.get("running"))


def _parse_updated_epoch(updated_at: str | None) -> float | None:
    if not updated_at:
        return None
    s = str(updated_at).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            # SQLite datetime('now') 为 UTC；按 UTC 解析避免时区误判 stale
            return time.mktime(time.strptime(s[:19], fmt)) - time.timezone
        except ValueError:
            continue
    return None


def reclaim_stale(kind: str, key: str, defaults: dict, *, stale_sec: int | None = None) -> dict:
    """若 running 且 updated_at 过旧，标记为失败并放行。年龄比较在 SQL 内完成，避免时区误判。"""
    limit = stale_sec if stale_sec is not None else _STALE_SEC
    con = db.connect()
    try:
        ensure_table(con)
        dialect = getattr(con, "dialect", "sqlite")
        err = f"任务超时未更新（>{limit}s），已自动回收"
        if dialect == "postgresql":
            con.execute(
                """UPDATE mesh_jobs SET running=0, done=0,
                   error=COALESCE(NULLIF(error,''), %s),
                   updated_at=to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                   WHERE job_kind=%s AND job_key=%s AND running=1
                   AND updated_at::timestamp < NOW() - (%s || ' seconds')::interval""",
                (err, kind, key, str(int(limit))),
            )
        else:
            # SQLite：updated_at 与 datetime('now') 同为 UTC 文本，直接比
            con.execute(
                """UPDATE mesh_jobs SET running=0, done=0,
                   error=COALESCE(NULLIF(error,''), ?),
                   updated_at=datetime('now')
                   WHERE job_kind=? AND job_key=? AND running=1
                   AND updated_at < datetime('now', ?)""",
                (err, kind, key, f"-{int(limit)} seconds"),
            )
        with db.write_lock():
            db.commit_retry(con)
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        con.close()
    return get(kind, key, defaults)


def try_claim(
    kind: str,
    key: str,
    defaults: dict,
    new_state: dict,
    *,
    force: bool = False,
    stale_sec: int | None = None,
) -> dict | None:
    """
    原子抢占：仅当当前未 running（或 force / 已 stale）时写入新 token 并返回状态。
    失败返回 None（已有活跃任务）。
    """
    from . import db_conn

    reclaim_stale(kind, key, defaults, stale_sec=stale_sec)
    con = db.connect()
    try:
        ensure_table(con)
        dialect = getattr(con, "dialect", "sqlite")
        row = con.execute(
            "SELECT token, running, done, error, payload_json, updated_at "
            "FROM mesh_jobs WHERE job_kind=? AND job_key=?",
            (kind, key),
        ).fetchone()
        cur = _row_to_state(row, defaults)
        if cur.get("running") and not force:
            return None

        prev_token = int(cur.get("token") or 0)
        token = prev_token + 1
        st = dict(new_state)
        st["token"] = token
        st["running"] = True
        st["done"] = False
        if force and cur.get("running"):
            st["_forced"] = True

        payload = {
            k: v
            for k, v in st.items()
            if k not in ("running", "done", "error", "token", "_updated_at")
        }
        body = json.dumps(payload, ensure_ascii=False)
        error = st.get("error")

        try:
            if dialect == "postgresql":
                if force:
                    cur2 = con.execute(
                        """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                           VALUES(?,?,?,1,0,?,?,to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
                           ON CONFLICT (job_kind, job_key) DO UPDATE SET
                             token=EXCLUDED.token, running=1, done=0, error=EXCLUDED.error,
                             payload_json=EXCLUDED.payload_json,
                             updated_at=to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                           WHERE mesh_jobs.token=%s""",
                        (kind, key, token, error, body, prev_token),
                    )
                else:
                    cur2 = con.execute(
                        """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                           VALUES(?,?,?,1,0,?,?,to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
                           ON CONFLICT (job_kind, job_key) DO UPDATE SET
                             token=EXCLUDED.token, running=1, done=0, error=EXCLUDED.error,
                             payload_json=EXCLUDED.payload_json,
                             updated_at=to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')
                           WHERE mesh_jobs.running=0 AND mesh_jobs.token=%s""",
                        (kind, key, token, error, body, prev_token),
                    )
            else:
                if force:
                    cur2 = con.execute(
                        """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                           VALUES(?,?,?,1,0,?,?,datetime('now'))
                           ON CONFLICT (job_kind, job_key) DO UPDATE SET
                             token=excluded.token, running=1, done=0, error=excluded.error,
                             payload_json=excluded.payload_json, updated_at=datetime('now')
                           WHERE mesh_jobs.token=?""",
                        (kind, key, token, error, body, prev_token),
                    )
                else:
                    cur2 = con.execute(
                        """INSERT INTO mesh_jobs(job_kind, job_key, token, running, done, error, payload_json, updated_at)
                           VALUES(?,?,?,1,0,?,?,datetime('now'))
                           ON CONFLICT (job_kind, job_key) DO UPDATE SET
                             token=excluded.token, running=1, done=0, error=excluded.error,
                             payload_json=excluded.payload_json, updated_at=datetime('now')
                           WHERE mesh_jobs.running=0 AND mesh_jobs.token=?""",
                        (kind, key, token, error, body, prev_token),
                    )
        except db_conn.IntegrityError:
            try:
                con.rollback()
            except Exception:
                pass
            return None

        # SQLite/PG：冲突后 WHERE 未满足时可能 rowcount=0
        if row is not None and getattr(cur2, "rowcount", 1) == 0:
            try:
                con.rollback()
            except Exception:
                pass
            return None

        # 确认本进程拿到了这个 token
        check = con.execute(
            "SELECT token, running FROM mesh_jobs WHERE job_kind=? AND job_key=?",
            (kind, key),
        ).fetchone()
        if not check or int(check["token"] or 0) != token or not check["running"]:
            try:
                con.rollback()
            except Exception:
                pass
            return None

        with db.write_lock():
            db.commit_retry(con)
        _cache_set(kind, key, st)
        return st
    finally:
        con.close()


def reclaim_dead_executors(current_holder: str) -> int:
    """Worker 启动时：回收「running 但执行者已死」的任务，避免僵尸占坑。

    条件：running=1 且 payload._executor 为其它 worker:*（非 pending/空/本进程）。
    标记 interrupted，清空 executor，便于 force 或人工重跑；不自动重入以免双写。
    """
    holder = (current_holder or "").strip()
    con = db.connect()
    n = 0
    try:
        ensure_table(con)
        rows = con.execute(
            "SELECT job_kind, job_key, token, payload_json, running FROM mesh_jobs WHERE running=1"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload_json"] or "{}") or {}
            except Exception:
                payload = {}
            ex = str(payload.get("_executor") or "").strip()
            if not ex or ex in ("pending",):
                continue
            if holder and ex == holder:
                continue
            # 仅回收明确绑定到 worker 进程的执行者
            if not ex.startswith("worker:"):
                continue
            kind = row["job_kind"]
            key = row["job_key"]
            defaults = {"running": False, "done": False, "error": None, "token": 0}
            st = get(kind, key, defaults)
            st["running"] = False
            st["done"] = False
            st["error"] = (
                f"执行者丢失（原 {ex}），任务已中断；请重新触发 Preview/Pipeline。"
            )
            st["final_status"] = "interrupted"
            st["_executor"] = ""
            put(kind, key, st)
            n += 1
            print(
                f"[mesh-jobs] reclaim_dead_executor kind={kind} key={key} was={ex}",
                flush=True,
            )
    finally:
        con.close()
    return n
