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


def is_postgres() -> bool:
    return bool(MESH_DB_URL) and MESH_DB_URL.lower().startswith("postgres")


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


class MeshConnection:
    """统一连接包装：? 占位符、Row/dict 行、execute().fetchone() 与 SQLite 一致。"""

    dialect: str

    def __init__(self, raw, dialect: str):
        self._raw = raw
        self.dialect = dialect

    def execute(self, sql: str, params=()):
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
        self._raw.close()

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


def connect() -> MeshConnection:
    if is_postgres():
        if not _HAS_PSYCOPG2:
            raise RuntimeError("MESH_DB_URL 已设置但未安装 psycopg2-binary")
        raw = psycopg2.connect(MESH_DB_URL)
        raw.autocommit = False
        return MeshConnection(raw, "postgresql")

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
    return MeshConnection(raw, "sqlite")


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
_maint_lock_path = Path(DB_PATH).parent / ".maintenance.lock"


def try_maintenance_lock(con) -> bool:
    """多 worker 时仅一个进程跑后台维护（PG advisory lock / 文件锁）。"""
    global _maint_lock_file
    if con.dialect == "postgresql":
        row = con.execute("SELECT pg_try_advisory_lock(%s)", (_MAINT_ADVISORY_KEY,)).fetchone()
        return bool(row and row.get("pg_try_advisory_lock"))
    try:
        import fcntl

        _maint_lock_path.parent.mkdir(parents=True, exist_ok=True)
        _maint_lock_file = open(_maint_lock_path, "w")
        fcntl.flock(_maint_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        if _maint_lock_file:
            _maint_lock_file.close()
            _maint_lock_file = None
        return False


def release_maintenance_lock(con) -> None:
    global _maint_lock_file
    if con.dialect == "postgresql":
        try:
            con.execute("SELECT pg_advisory_unlock(%s)", (_MAINT_ADVISORY_KEY,))
        except Exception:
            pass
        return
    if _maint_lock_file:
        try:
            import fcntl

            fcntl.flock(_maint_lock_file.fileno(), fcntl.LOCK_UN)
            _maint_lock_file.close()
        except Exception:
            pass
        _maint_lock_file = None
