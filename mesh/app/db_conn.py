"""数据库连接：SQLite（默认）或 PostgreSQL（MESH_DB_URL）。"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from pathlib import Path

DB_PATH = os.environ.get("MESH_DB", str(Path(__file__).resolve().parent.parent / "data" / "mesh.db"))
MESH_DB_URL = (os.environ.get("MESH_DB_URL") or "").strip()

try:
    import psycopg2
    import psycopg2.extras

    _HAS_PSYCOPG2 = True
except ImportError:
    psycopg2 = None  # type: ignore
    _HAS_PSYCOPG2 = False

IntegrityError: tuple[type[BaseException], ...] = (sqlite3.IntegrityError,)
if _HAS_PSYCOPG2:
    IntegrityError = (sqlite3.IntegrityError, psycopg2.IntegrityError)  # type: ignore

_PG_POOL = None
_pg_pool_lock = threading.Lock()


def is_postgres() -> bool:
    return bool(MESH_DB_URL) and MESH_DB_URL.lower().startswith("postgres")


def get_pg_pool():
    """进程内 ThreadedConnectionPool，多请求复用连接。"""
    global _PG_POOL
    if not is_postgres():
        return None
    if not _HAS_PSYCOPG2:
        raise RuntimeError("MESH_DB_URL 已设置但未安装 psycopg2-binary")
    with _pg_pool_lock:
        if _PG_POOL is None:
            from psycopg2 import pool as pg_pool

            # 池上限低于并发时，高并发会卡在 getconn() 等待，表现为「整轮变慢」。
            # 默认对齐 web 侧飞书 job 并发（MESH_FEISHU_JOB_WORKERS 默认 32）；
            # max 只是上限、按需增长，worker 进程实际不会占满。
            # 注意：PG max_connections=100，且 web+worker 共用一份 .env，
            # 所以默认值不能贴着并发数往上加；可用 MESH_DB_POOL_MAX 覆盖。
            mn = max(1, int(os.environ.get("MESH_DB_POOL_MIN", "2") or "2"))
            mx = max(mn, int(os.environ.get("MESH_DB_POOL_MAX", "32") or "32"))
            _PG_POOL = pg_pool.ThreadedConnectionPool(mn, mx, MESH_DB_URL)
            print(f"[mesh] pg pool min={mn} max={mx}", flush=True)
        return _PG_POOL


def close_pool() -> None:
    global _PG_POOL
    with _pg_pool_lock:
        if _PG_POOL is not None:
            try:
                _PG_POOL.closeall()
            except Exception:
                pass
            _PG_POOL = None


def sql_now(dialect: str = "sqlite") -> str:
    if dialect == "postgresql":
        return "to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')"
    return "datetime('now')"


def adapt_sql(sql: str, dialect: str) -> str:
    if dialect != "postgresql":
        return sql
    s = sql
    s = re.sub(
        r"datetime\s*\(\s*['\"]now['\"]\s*\)",
        "to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS')",
        s,
        flags=re.IGNORECASE,
    )
    if "?" in s:
        s = s.replace("?", "%s")
    # PostgreSQL 保留字：edits.user
    s = re.sub(r"(INSERT INTO edits\(issue_id,)user(,target)", r'\1"user"\2', s, flags=re.IGNORECASE)
    s = re.sub(r"(SELECT at, )user(, target FROM edits)", r'\1"user"\2', s, flags=re.IGNORECASE)
    s = re.sub(r"(SELECT e\.at, e\.)user(, e\.target)", r'\1"user"\2', s, flags=re.IGNORECASE)
    return s


class _PgCursor:
    def __init__(self, cur):
        self._cur = cur

    @property
    def rowcount(self) -> int:
        return self._cur.rowcount

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def __iter__(self):
        return iter(self._cur)

    @property
    def lastrowid(self) -> int | None:
        """兼容 SQLite；PG 仅在 INSERT … RETURNING 后由 insert_id 设置。"""
        return getattr(self, "_lastrowid", None)


class MeshConnection:
    """统一连接包装：? 占位符、Row/dict 行、execute().fetchone() 与 SQLite 一致。"""

    dialect: str

    def __init__(self, raw, dialect: str, *, pooled: bool = False):
        self._raw = raw
        self.dialect = dialect
        self._pooled = pooled
        self._closed = False

    def execute(self, sql: str, params=()):
        from .publish_lane import guard_sql_against_published_write

        guard_sql_against_published_write(sql)
        sql = adapt_sql(sql, self.dialect)
        if self.dialect == "postgresql":
            cur = self._raw.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute(sql, params or None)
            return _PgCursor(cur)
        return self._raw.execute(sql, params)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pooled and self.dialect == "postgresql":
            try:
                self._raw.rollback()
            except Exception:
                pass
            try:
                get_pg_pool().putconn(self._raw)
            except Exception:
                try:
                    self._raw.close()
                except Exception:
                    pass
            return
        try:
            self._raw.close()
        except Exception:
            pass

    def executescript(self, script: str):
        if self.dialect == "sqlite":
            self._raw.executescript(script)
            return
        for stmt in _split_sql(script):
            if stmt.strip():
                self.execute(stmt)


def _split_sql(script: str) -> list[str]:
    parts: list[str] = []
    buf: list[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.startswith("--"):
            continue
        buf.append(line)
        if ";" in line:
            chunk = "\n".join(buf)
            for piece in chunk.split(";"):
                piece = piece.strip()
                if piece:
                    parts.append(piece)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        parts.append(tail)
    return parts


def insert_id(con: MeshConnection, sql: str, params=()) -> int:
    """INSERT 并返回自增 id（SQLite lastrowid / PostgreSQL RETURNING id）。"""
    if con.dialect == "postgresql":
        s = adapt_sql(sql, "postgresql").rstrip().rstrip(";")
        if "returning" not in s.lower():
            s += " RETURNING id"
        cur = con.execute(s, params)
        row = cur.fetchone()
        if not row or row.get("id") is None:
            raise RuntimeError("INSERT RETURNING id 未返回行")
        return int(row["id"])
    cur = con.execute(sql, params)
    rid = getattr(cur, "lastrowid", None)
    if not rid:
        raise RuntimeError("INSERT lastrowid 不可用")
    return int(rid)


def connect() -> MeshConnection:
    if is_postgres():
        raw = get_pg_pool().getconn()
        raw.autocommit = False
        return MeshConnection(raw, "postgresql", pooled=True)

    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(DB_PATH, check_same_thread=False)
    raw.row_factory = sqlite3.Row
    for pragma in (
        "PRAGMA journal_mode=WAL",
        "PRAGMA synchronous=NORMAL",
        "PRAGMA busy_timeout=30000",
    ):
        try:
            raw.execute(pragma)
        except Exception:
            pass
    return MeshConnection(raw, "sqlite", pooled=False)


def chunk_index_upsert_sql(dialect: str) -> str:
    cols = (
        "chunk_id, issue_slug, issue_id, date_end, layer, section, stype, owner_team,"
        " entity_name, title, body, source_label, item_id, source_id, toks, meta_json"
    )
    ph = ", ".join(["%s"] * 16) if dialect == "postgresql" else ", ".join(["?"] * 16)
    if dialect == "postgresql":
        return f"""INSERT INTO chunk_index({cols}) VALUES ({ph})
            ON CONFLICT (chunk_id) DO UPDATE SET
              issue_slug=EXCLUDED.issue_slug, issue_id=EXCLUDED.issue_id,
              date_end=EXCLUDED.date_end, layer=EXCLUDED.layer, section=EXCLUDED.section,
              stype=EXCLUDED.stype, owner_team=EXCLUDED.owner_team,
              entity_name=EXCLUDED.entity_name, title=EXCLUDED.title, body=EXCLUDED.body,
              source_label=EXCLUDED.source_label, item_id=EXCLUDED.item_id,
              source_id=EXCLUDED.source_id, toks=EXCLUDED.toks, meta_json=EXCLUDED.meta_json"""
    return f"INSERT OR REPLACE INTO chunk_index({cols}) VALUES ({ph})"


def chunk_embedding_upsert_sql(dialect: str) -> str:
    if dialect == "postgresql":
        return """INSERT INTO chunk_embeddings(chunk_id, model, dim, vector_json, updated_at)
            VALUES (%s,%s,%s,%s,to_char(NOW(), 'YYYY-MM-DD HH24:MI:SS'))
            ON CONFLICT (chunk_id) DO UPDATE SET
              model=EXCLUDED.model, dim=EXCLUDED.dim, vector_json=EXCLUDED.vector_json,
              updated_at=EXCLUDED.updated_at"""
    return """INSERT OR REPLACE INTO chunk_embeddings(chunk_id, model, dim, vector_json, updated_at)
        VALUES (?,?,?,?,datetime('now'))"""


_MAINT_ADVISORY_KEY = 0x4D455348  # 'MESH'
_maint_lock_file = None
_maint_advisory_held = False
_maint_lock_path = Path(DB_PATH).parent / ".maintenance.lock"


def try_maintenance_lock(con) -> bool:
    """多 worker 时仅一个进程跑后台维护（PG advisory lock / 文件锁）。"""
    global _maint_lock_file, _maint_advisory_held
    _maint_advisory_held = False
    if con.dialect == "postgresql":
        row = con.execute("SELECT pg_try_advisory_lock(%s)", (_MAINT_ADVISORY_KEY,)).fetchone()
        ok = bool(row and row.get("pg_try_advisory_lock"))
        if ok:
            _maint_advisory_held = True
        return ok
    try:
        import fcntl

        _maint_lock_path.parent.mkdir(parents=True, exist_ok=True)
        _maint_lock_file = open(_maint_lock_path, "w")
        fcntl.flock(_maint_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _maint_advisory_held = True
        return True
    except OSError:
        if _maint_lock_file:
            _maint_lock_file.close()
            _maint_lock_file = None
        return False


def release_maintenance_lock(con) -> None:
    global _maint_lock_file, _maint_advisory_held
    if con.dialect == "postgresql":
        if not _maint_advisory_held:
            return
        try:
            con.execute("SELECT pg_advisory_unlock(%s)", (_MAINT_ADVISORY_KEY,))
        except Exception:
            pass
        _maint_advisory_held = False
        return
    if not _maint_advisory_held:
        return
    if _maint_lock_file:
        try:
            import fcntl

            fcntl.flock(_maint_lock_file.fileno(), fcntl.LOCK_UN)
            _maint_lock_file.close()
        except Exception:
            pass
        _maint_lock_file = None
    _maint_advisory_held = False
