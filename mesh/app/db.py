"""GeekPark Mesh · 数据库层（SQLite 或 PostgreSQL via MESH_DB_URL）"""
import sqlite3, json, os, hashlib, secrets, datetime, re, threading, logging, time
from contextlib import contextmanager
from pathlib import Path

from . import db_conn

_log = logging.getLogger("mesh.db")
DB_PATH = db_conn.DB_PATH
SCHEMA_VERSION = "1.9.0"
_DB_WRITE_LOCK = threading.RLock()

IntegrityError = db_conn.IntegrityError
is_postgres = db_conn.is_postgres
connect = db_conn.connect
insert_id = db_conn.insert_id
try_maintenance_lock = db_conn.try_maintenance_lock
release_maintenance_lock = db_conn.release_maintenance_lock

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, username TEXT UNIQUE, display TEXT, pw_hash TEXT,
  role TEXT NOT NULL DEFAULT 'viewer',      -- owner | admin | editor | viewer（dept 已废弃）
  team TEXT,                                 -- 用户所属团队（dept 视角过滤用，可选）
  feishu_open_id TEXT UNIQUE,
  avatar_url TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS issues(
  id INTEGER PRIMARY KEY, slug TEXT UNIQUE, date_start TEXT, date_end TEXT, period_label TEXT,
  version TEXT DEFAULT 'v1.4', status TEXT DEFAULT 'draft',   -- draft | published
  draft_json TEXT, published_json TEXT, published_items_snapshot TEXT,
  updated_at TEXT, published_at TEXT, created_at TEXT DEFAULT (datetime('now')),
  embedding_status TEXT DEFAULT '', embedding_total INTEGER DEFAULT 0,
  embedding_done INTEGER DEFAULT 0, embedding_model TEXT DEFAULT '',
  embedding_at TEXT, embedding_error TEXT
);
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, issue_id INTEGER, stype TEXT, team TEXT, title TEXT, filename TEXT, raw_path TEXT,
  text TEXT, meta TEXT, fetched_at TEXT DEFAULT (datetime('now')), extracted INTEGER DEFAULT 0,
  channel TEXT DEFAULT 'manual'              -- aggregator | manual；只留痕，不参与判断
);
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, issue_id INTEGER, source_id INTEGER, team TEXT, stype TEXT,
  zone INTEGER, level TEXT, kind TEXT,           -- kind: fact | judgment
  text TEXT, entities TEXT, roles TEXT, signals TEXT, source_label TEXT, pointer TEXT,
  blocked INTEGER DEFAULT 0,                     -- 1 = ⑤区/L3，永不进分发与索引
  owner_team TEXT,                               -- 归属团队（条目级判定；聚合文档是混合体，不能按文件挂）
  channel TEXT DEFAULT 'manual',                 -- 继承自 source
  merged_into INTEGER,                           -- 跨通道合并后指向主条目 id；非空者不进后续管线
  source_labels TEXT,                            -- 合并后的全部来源行 JSON 数组
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS entities(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, kind TEXT, roles TEXT, first_issue TEXT, listed INTEGER DEFAULT 0, aliases TEXT
);
CREATE TABLE IF NOT EXISTS cards(                -- 各团队要点卡（生成即 approved，供审校展示）
  id INTEGER PRIMARY KEY, issue_id INTEGER, team TEXT, card_json TEXT, status TEXT DEFAULT 'approved', -- approved|rejected
  reviewer TEXT, reviewed_at TEXT, created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS edits(
  id INTEGER PRIMARY KEY, issue_id INTEGER, user TEXT, target TEXT, before TEXT, after TEXT, at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS mail_log(
  id INTEGER PRIMARY KEY, issue_id INTEGER, to_addr TEXT, subject TEXT, ok INTEGER, error TEXT, at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS versions(          -- 上线版本，不可改；一次上线一行，永不 UPDATE
  id INTEGER PRIMARY KEY, issue_id INTEGER, version TEXT, at TEXT DEFAULT (datetime('now')),
  by_user TEXT, cards_out INTEGER, edited_count INTEGER, url TEXT, snapshot_json TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(
  issue_slug UNINDEXED,
  section UNINDEXED,
  title UNINDEXED,
  body UNINDEXED,
  date_end UNINDEXED,
  toks,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS entity_team_facts(
  id INTEGER PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  date_start TEXT,
  date_end TEXT,
  section TEXT NOT NULL,          -- 接触 | 关注 | 关系 | 看法
  name TEXT NOT NULL,
  team TEXT NOT NULL,
  kind TEXT DEFAULT 'unknown',    -- person | company | topic | unknown
  group_title TEXT,
  snippet TEXT,
  source_hint TEXT
);
CREATE INDEX IF NOT EXISTS idx_etf_team_name ON entity_team_facts(team, name);
CREATE INDEX IF NOT EXISTS idx_etf_name_team ON entity_team_facts(name, team);
CREATE INDEX IF NOT EXISTS idx_etf_date_team ON entity_team_facts(date_end, team);
CREATE INDEX IF NOT EXISTS idx_etf_section_team ON entity_team_facts(section, team);
CREATE INDEX IF NOT EXISTS idx_etf_slug ON entity_team_facts(issue_slug);
CREATE TABLE IF NOT EXISTS item_facts(
  id INTEGER PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_start TEXT,
  date_end TEXT,
  item_id INTEGER,
  source_id INTEGER,
  owner_team TEXT NOT NULL,
  stype TEXT,
  zone INTEGER,
  level TEXT,
  kind TEXT,
  primary_name TEXT,
  entities_json TEXT,
  roles_json TEXT,
  signals_json TEXT,
  text_snippet TEXT,
  source_label TEXT,
  channel TEXT DEFAULT 'manual',
  toks TEXT,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_if_slug ON item_facts(issue_slug);
CREATE INDEX IF NOT EXISTS idx_if_team ON item_facts(owner_team);
CREATE INDEX IF NOT EXISTS idx_if_name ON item_facts(primary_name);
CREATE INDEX IF NOT EXISTS idx_if_date ON item_facts(date_end);
CREATE INDEX IF NOT EXISTS idx_if_stype ON item_facts(stype);
CREATE INDEX IF NOT EXISTS idx_if_item ON item_facts(item_id);
CREATE VIRTUAL TABLE IF NOT EXISTS item_facts_fts USING fts5(
  issue_slug UNINDEXED,
  date_end UNINDEXED,
  item_id UNINDEXED,
  source_id UNINDEXED,
  owner_team UNINDEXED,
  stype UNINDEXED,
  primary_name UNINDEXED,
  text_snippet UNINDEXED,
  source_label UNINDEXED,
  level UNINDEXED,
  kind UNINDEXED,
  zone UNINDEXED,
  toks,
  tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS item_entity_facts(
  id INTEGER PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_start TEXT,
  date_end TEXT,
  item_id INTEGER NOT NULL,
  source_id INTEGER,
  owner_team TEXT NOT NULL,
  stype TEXT,
  entity_name TEXT NOT NULL,
  entity_kind TEXT DEFAULT 'unknown',
  text_snippet TEXT,
  source_label TEXT,
  channel TEXT DEFAULT 'manual'
);
CREATE INDEX IF NOT EXISTS idx_ief_slug ON item_entity_facts(issue_slug);
CREATE INDEX IF NOT EXISTS idx_ief_name ON item_entity_facts(entity_name);
CREATE INDEX IF NOT EXISTS idx_ief_team_name ON item_entity_facts(owner_team, entity_name);
CREATE INDEX IF NOT EXISTS idx_ief_date ON item_entity_facts(date_end);
CREATE INDEX IF NOT EXISTS idx_ief_item ON item_entity_facts(item_id);
CREATE TABLE IF NOT EXISTS feishu_chat_bindings(
  chat_id TEXT PRIMARY KEY,
  chat_type TEXT DEFAULT 'group',
  team TEXT,
  label TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sources_issue ON sources(issue_id);
CREATE INDEX IF NOT EXISTS idx_items_issue ON items(issue_id);
CREATE INDEX IF NOT EXISTS idx_items_owner ON items(issue_id, owner_team);
CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
CREATE INDEX IF NOT EXISTS idx_cards_issue ON cards(issue_id);
CREATE TABLE IF NOT EXISTS chunk_index(
  chunk_id TEXT PRIMARY KEY,
  issue_slug TEXT NOT NULL,
  issue_id INTEGER,
  date_end TEXT,
  layer TEXT NOT NULL,
  section TEXT,
  stype TEXT,
  owner_team TEXT,
  entity_name TEXT,
  title TEXT,
  body TEXT NOT NULL,
  source_label TEXT,
  item_id INTEGER,
  source_id INTEGER,
  toks TEXT,
  meta_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_chunk_slug ON chunk_index(issue_slug);
CREATE INDEX IF NOT EXISTS idx_chunk_team ON chunk_index(owner_team);
CREATE INDEX IF NOT EXISTS idx_chunk_date ON chunk_index(date_end);
CREATE INDEX IF NOT EXISTS idx_chunk_layer ON chunk_index(layer);
CREATE TABLE IF NOT EXISTS chunk_embeddings(
  chunk_id TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  dim INTEGER NOT NULL,
  vector_json TEXT NOT NULL,
  updated_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ask_sessions(
  id TEXT PRIMARY KEY,
  scope_key TEXT NOT NULL UNIQUE,
  channel TEXT NOT NULL,
  user_id INTEGER,
  feishu_open_id TEXT,
  chat_id TEXT,
  thread_id TEXT,
  team_scope TEXT,
  title TEXT,
  meta_json TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT,
  last_active_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_ask_sess_user ON ask_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_ask_sess_chat ON ask_sessions(chat_id);
CREATE INDEX IF NOT EXISTS idx_ask_sess_scope ON ask_sessions(scope_key);
CREATE TABLE IF NOT EXISTS ask_messages(
  id INTEGER PRIMARY KEY,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  mode TEXT,
  n_context INTEGER,
  meta_json TEXT,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ask_msg_sess ON ask_messages(session_id, id);
CREATE TABLE IF NOT EXISTS ask_log(
  id INTEGER PRIMARY KEY,
  session_id TEXT,
  user_id INTEGER,
  feishu_open_id TEXT,
  chat_id TEXT,
  thread_id TEXT,
  team_scope TEXT,
  query TEXT NOT NULL,
  mode TEXT,
  n_hits INTEGER,
  latency_ms INTEGER,
  created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ask_log_user ON ask_log(user_id, created_at);
CREATE TABLE IF NOT EXISTS user_ask_presets(
  id INTEGER PRIMARY KEY,
  scope TEXT NOT NULL DEFAULT 'user',
  user_id INTEGER,
  target_team TEXT,
  title TEXT NOT NULL,
  question_template TEXT NOT NULL,
  schedule TEXT DEFAULT 'manual',
  schedule_time TEXT,
  schedule_dow INTEGER,
  team_scope TEXT,
  enabled INTEGER DEFAULT 1,
  created_by INTEGER,
  last_pushed_at TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_presets_user ON user_ask_presets(user_id, enabled);
CREATE INDEX IF NOT EXISTS idx_presets_team ON user_ask_presets(target_team, enabled);
CREATE TABLE IF NOT EXISTS preset_push_log(
  id INTEGER PRIMARY KEY,
  preset_id INTEGER NOT NULL,
  user_id INTEGER,
  answer_snippet TEXT,
  ok INTEGER DEFAULT 1,
  error TEXT,
  pushed_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS crm_sync_state(
  kind TEXT PRIMARY KEY,
  database_id TEXT,
  database_title TEXT,
  cursor_last_edited TEXT,
  last_full_at TEXT,
  last_incr_at TEXT,
  row_count INTEGER DEFAULT 0,
  status TEXT DEFAULT '',
  error TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS crm_companies(
  id INTEGER PRIMARY KEY,
  notion_id TEXT UNIQUE NOT NULL,
  name TEXT, aliases TEXT, one_liner TEXT, sector TEXT, stage TEXT, website TEXT,
  people_ids_json TEXT, props_json TEXT, last_edited_time TEXT, synced_at TEXT
);
CREATE TABLE IF NOT EXISTS crm_people(
  id INTEGER PRIMARY KEY,
  notion_id TEXT UNIQUE NOT NULL,
  display_name TEXT, aliases TEXT, headline TEXT,
  company_ids_json TEXT, company_names TEXT, sector TEXT, location TEXT,
  email TEXT, wechat TEXT, linkedin TEXT,
  interaction_ids_json TEXT, take_ids_json TEXT,
  interaction_count TEXT, last_touched TEXT,
  props_json TEXT, last_edited_time TEXT, synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_crm_people_name ON crm_people(display_name);
CREATE INDEX IF NOT EXISTS idx_crm_people_touched ON crm_people(last_touched);
CREATE INDEX IF NOT EXISTS idx_crm_companies_name ON crm_companies(name);
CREATE TABLE IF NOT EXISTS crm_interactions(
  id INTEGER PRIMARY KEY,
  notion_id TEXT UNIQUE NOT NULL,
  title TEXT, date_start TEXT, interact_type TEXT,
  people_ids_json TEXT, people_names TEXT, our_side TEXT, output_link TEXT,
  processed INTEGER DEFAULT 0, props_json TEXT, last_edited_time TEXT, synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_crm_ix_date ON crm_interactions(date_start);
CREATE TABLE IF NOT EXISTS crm_takes(
  id INTEGER PRIMARY KEY,
  notion_id TEXT UNIQUE NOT NULL,
  name TEXT, person_ids_json TEXT, person_names TEXT, verdict TEXT,
  scenario TEXT, owner TEXT, last_reviewed TEXT, is_prospect TEXT,
  props_json TEXT, last_edited_time TEXT, synced_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_crm_takes_reviewed ON crm_takes(last_reviewed);
"""

@contextmanager
def write_lock():
    """跨 pipeline / preview / 后台维护 的写锁（PG 下协调长事务）。"""
    with _DB_WRITE_LOCK:
        yield


def commit_retry(con, *, max_attempts: int = 5) -> None:
    """写冲突时指数退避重试（SQLite locked / PG deadlock & serialization）。"""
    delay = 0.05
    for attempt in range(max_attempts):
        try:
            con.commit()
            return
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            if attempt >= max_attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)
        except Exception as e:
            if getattr(con, "dialect", "sqlite") != "postgresql" or not _pg_commit_retriable(e):
                raise
            try:
                con.rollback()
            except Exception:
                pass
            if attempt >= max_attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 1.0)


def _pg_commit_retriable(exc: BaseException) -> bool:
    try:
        import psycopg2
        from psycopg2 import errors as pg_errors

        return isinstance(
            exc,
            (
                psycopg2.extensions.TransactionRollbackError,
                pg_errors.DeadlockDetected,
                pg_errors.SerializationFailure,
                pg_errors.LockNotAvailable,
            ),
        )
    except Exception:
        return False

def hash_pw(pw: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120000).hex()
    return f"{salt}${h}"

def check_pw(pw: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
        return hash_pw(pw, salt) == stored
    except Exception:
        return False

def _cols(con, table):
    if getattr(con, "dialect", "sqlite") == "postgresql":
        rows = con.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s",
            (table,),
        ).fetchall()
        return {r["column_name"] for r in rows}
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def migrate(con):
    """就地升级旧库：老部署直接覆盖代码后启动即可，不丢数据。"""
    _embedding_cols = [
        ("issues", "embedding_status", "TEXT DEFAULT ''"),
        ("issues", "embedding_total", "INTEGER DEFAULT 0"),
        ("issues", "embedding_done", "INTEGER DEFAULT 0"),
        ("issues", "embedding_model", "TEXT DEFAULT ''"),
        ("issues", "embedding_at", "TEXT"),
        ("issues", "embedding_error", "TEXT"),
    ]
    if is_postgres():
        pg_add = [
            ("items", "owner_provenance", "TEXT"),
            ("items", "llm_owner_team_hint", "TEXT"),
        ] + _embedding_cols
        for table, col, decl in pg_add:
            try:
                if col not in _cols(con, table):
                    con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
            except Exception:
                pass
        try:
            from . import notion_crm

            notion_crm.ensure_crm_schema(con)
        except Exception:
            pass
        try:
            set_setting(con, "schema_version", SCHEMA_VERSION)
        except Exception:
            pass
        ensure_search_fts_schema(con)
        return
    add = [
        ("sources", "channel", "TEXT DEFAULT 'manual'"),
        ("items", "owner_team", "TEXT"),
        ("items", "channel", "TEXT DEFAULT 'manual'"),
        ("items", "merged_into", "INTEGER"),
        ("items", "source_labels", "TEXT"),
        ("items", "owner_provenance", "TEXT"),
        ("items", "llm_owner_team_hint", "TEXT"),
        ("users", "avatar_url", "TEXT"),
        ("issues", "published_items_snapshot", "TEXT"),
    ] + _embedding_cols
    for table, col, decl in add:
        try:
            if col not in _cols(con, table):
                con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
        except Exception:
            pass
    # 旧数据回填：owner_team 缺失时先沿用 team，管理员可在审校台改
    try:
        con.execute("UPDATE items SET owner_team=team WHERE owner_team IS NULL OR owner_team=''")
    except Exception:
        pass
    # 交叉问答事实表（旧库无 SCHEMA 里新表时补建）
    con.execute("""CREATE TABLE IF NOT EXISTS entity_team_facts(
      id INTEGER PRIMARY KEY, issue_slug TEXT NOT NULL, date_start TEXT, date_end TEXT,
      section TEXT NOT NULL, name TEXT NOT NULL, team TEXT NOT NULL, kind TEXT DEFAULT 'unknown',
      group_title TEXT, snippet TEXT, source_hint TEXT)""")
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_etf_team_name ON entity_team_facts(team, name)",
        "CREATE INDEX IF NOT EXISTS idx_etf_name_team ON entity_team_facts(name, team)",
        "CREATE INDEX IF NOT EXISTS idx_etf_date_team ON entity_team_facts(date_end, team)",
        "CREATE INDEX IF NOT EXISTS idx_etf_section_team ON entity_team_facts(section, team)",
        "CREATE INDEX IF NOT EXISTS idx_etf_slug ON entity_team_facts(issue_slug)",
    ):
        try:
            con.execute(idx_sql)
        except Exception:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS item_facts(
      id INTEGER PRIMARY KEY, issue_slug TEXT NOT NULL, issue_id INTEGER,
      date_start TEXT, date_end TEXT, item_id INTEGER, source_id INTEGER,
      owner_team TEXT NOT NULL, stype TEXT, zone INTEGER, level TEXT, kind TEXT,
      primary_name TEXT, entities_json TEXT, roles_json TEXT, signals_json TEXT,
      text_snippet TEXT, source_label TEXT, channel TEXT DEFAULT 'manual',
      toks TEXT, meta_json TEXT)""")
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_if_slug ON item_facts(issue_slug)",
        "CREATE INDEX IF NOT EXISTS idx_if_team ON item_facts(owner_team)",
        "CREATE INDEX IF NOT EXISTS idx_if_name ON item_facts(primary_name)",
        "CREATE INDEX IF NOT EXISTS idx_if_date ON item_facts(date_end)",
        "CREATE INDEX IF NOT EXISTS idx_if_stype ON item_facts(stype)",
        "CREATE INDEX IF NOT EXISTS idx_if_item ON item_facts(item_id)",
    ):
        try:
            con.execute(idx_sql)
        except Exception:
            pass
    try:
        con.execute("SELECT 1 FROM item_facts_fts LIMIT 1")
    except Exception:
        con.execute("""CREATE VIRTUAL TABLE item_facts_fts USING fts5(
          issue_slug UNINDEXED, date_end UNINDEXED, item_id UNINDEXED, source_id UNINDEXED,
          owner_team UNINDEXED, stype UNINDEXED, primary_name UNINDEXED,
          text_snippet UNINDEXED, source_label UNINDEXED, level UNINDEXED, kind UNINDEXED,
          zone UNINDEXED, toks, tokenize='unicode61')""")
    con.execute("""CREATE TABLE IF NOT EXISTS item_entity_facts(
      id INTEGER PRIMARY KEY, issue_slug TEXT NOT NULL, issue_id INTEGER,
      date_start TEXT, date_end TEXT, item_id INTEGER NOT NULL, source_id INTEGER,
      owner_team TEXT NOT NULL, stype TEXT, entity_name TEXT NOT NULL,
      entity_kind TEXT DEFAULT 'unknown', text_snippet TEXT, source_label TEXT,
      channel TEXT DEFAULT 'manual')""")
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_ief_slug ON item_entity_facts(issue_slug)",
        "CREATE INDEX IF NOT EXISTS idx_ief_name ON item_entity_facts(entity_name)",
        "CREATE INDEX IF NOT EXISTS idx_ief_team_name ON item_entity_facts(owner_team, entity_name)",
        "CREATE INDEX IF NOT EXISTS idx_ief_date ON item_entity_facts(date_end)",
        "CREATE INDEX IF NOT EXISTS idx_ief_item ON item_entity_facts(item_id)",
        "CREATE INDEX IF NOT EXISTS idx_sources_issue ON sources(issue_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_issue ON items(issue_id)",
        "CREATE INDEX IF NOT EXISTS idx_items_owner ON items(issue_id, owner_team)",
        "CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id)",
        "CREATE INDEX IF NOT EXISTS idx_cards_issue ON cards(issue_id)",
    ):
        try:
            con.execute(idx_sql)
        except Exception:
            pass
    con.execute("""CREATE TABLE IF NOT EXISTS feishu_chat_bindings(
      chat_id TEXT PRIMARY KEY, chat_type TEXT DEFAULT 'group', team TEXT, label TEXT,
      created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)""")
    for ddl in (
        """CREATE TABLE IF NOT EXISTS chunk_index(
          chunk_id TEXT PRIMARY KEY, issue_slug TEXT NOT NULL, issue_id INTEGER, date_end TEXT,
          layer TEXT NOT NULL, section TEXT, stype TEXT, owner_team TEXT, entity_name TEXT,
          title TEXT, body TEXT NOT NULL, source_label TEXT, item_id INTEGER, source_id INTEGER,
          toks TEXT, meta_json TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_chunk_slug ON chunk_index(issue_slug)",
        "CREATE INDEX IF NOT EXISTS idx_chunk_team ON chunk_index(owner_team)",
        "CREATE INDEX IF NOT EXISTS idx_chunk_date ON chunk_index(date_end)",
        "CREATE INDEX IF NOT EXISTS idx_chunk_layer ON chunk_index(layer)",
        """CREATE TABLE IF NOT EXISTS chunk_embeddings(
          chunk_id TEXT PRIMARY KEY, model TEXT NOT NULL, dim INTEGER NOT NULL,
          vector_json TEXT NOT NULL, updated_at TEXT DEFAULT (datetime('now')))""",
        """CREATE TABLE IF NOT EXISTS ask_sessions(
          id TEXT PRIMARY KEY, scope_key TEXT NOT NULL UNIQUE, channel TEXT NOT NULL,
          user_id INTEGER, feishu_open_id TEXT, chat_id TEXT, thread_id TEXT, team_scope TEXT,
          title TEXT, meta_json TEXT, created_at TEXT DEFAULT (datetime('now')),
          updated_at TEXT, last_active_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_ask_sess_user ON ask_sessions(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_ask_sess_chat ON ask_sessions(chat_id)",
        "CREATE INDEX IF NOT EXISTS idx_ask_sess_scope ON ask_sessions(scope_key)",
        """CREATE TABLE IF NOT EXISTS ask_messages(
          id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL,
          mode TEXT, n_context INTEGER, meta_json TEXT,
          created_at TEXT DEFAULT (datetime('now')))""",
        "CREATE INDEX IF NOT EXISTS idx_ask_msg_sess ON ask_messages(session_id, id)",
        """CREATE TABLE IF NOT EXISTS ask_analyses(
          analysis_id TEXT PRIMARY KEY,
          parent_analysis_id TEXT,
          session_id TEXT,
          user_id INTEGER,
          question TEXT,
          status TEXT NOT NULL DEFAULT 'running',
          answer TEXT,
          context_refs_json TEXT,
          usage_json TEXT,
          verify_json TEXT,
          sources_json TEXT,
          created_at TEXT,
          updated_at TEXT,
          finished_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_ask_analyses_sess ON ask_analyses(session_id, created_at)",
        """CREATE TABLE IF NOT EXISTS ask_log(
          id INTEGER PRIMARY KEY, session_id TEXT, user_id INTEGER, feishu_open_id TEXT,
          chat_id TEXT, thread_id TEXT, team_scope TEXT, query TEXT NOT NULL, mode TEXT,
          n_hits INTEGER, latency_ms INTEGER, created_at TEXT DEFAULT (datetime('now')))""",
        "CREATE INDEX IF NOT EXISTS idx_ask_log_user ON ask_log(user_id, created_at)",
        """CREATE TABLE IF NOT EXISTS user_ask_presets(
          id INTEGER PRIMARY KEY, scope TEXT NOT NULL DEFAULT 'user', user_id INTEGER, target_team TEXT,
          title TEXT NOT NULL, question_template TEXT NOT NULL, schedule TEXT DEFAULT 'manual',
          schedule_time TEXT, schedule_dow INTEGER, team_scope TEXT, enabled INTEGER DEFAULT 1,
          created_by INTEGER, last_pushed_at TEXT,
          created_at TEXT DEFAULT (datetime('now')), updated_at TEXT)""",
        "CREATE INDEX IF NOT EXISTS idx_presets_user ON user_ask_presets(user_id, enabled)",
        "CREATE INDEX IF NOT EXISTS idx_presets_team ON user_ask_presets(target_team, enabled)",
        """CREATE TABLE IF NOT EXISTS preset_push_log(
          id INTEGER PRIMARY KEY, preset_id INTEGER NOT NULL, user_id INTEGER,
          answer_snippet TEXT, ok INTEGER DEFAULT 1, error TEXT,
          pushed_at TEXT DEFAULT (datetime('now')))""",
        """CREATE TABLE IF NOT EXISTS mesh_jobs(
          job_kind TEXT NOT NULL, job_key TEXT NOT NULL,
          token INTEGER NOT NULL DEFAULT 0, running INTEGER NOT NULL DEFAULT 0,
          done INTEGER NOT NULL DEFAULT 0, error TEXT, payload_json TEXT,
          updated_at TEXT DEFAULT (datetime('now')),
          PRIMARY KEY (job_kind, job_key))""",
        """CREATE TABLE IF NOT EXISTS ask_rate_hits(
          rate_key TEXT NOT NULL, hit_at REAL NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS idx_ask_rate_key ON ask_rate_hits(rate_key, hit_at)",
        """CREATE TABLE IF NOT EXISTS mesh_llm_slots(
          slot_id INTEGER PRIMARY KEY, holder TEXT, taken_at REAL)""",
    ):
        try:
            con.execute(ddl)
        except Exception:
            pass
    try:
        from . import notion_crm

        notion_crm.ensure_crm_schema(con)
    except Exception:
        pass
    try:
        set_setting(con, "schema_version", SCHEMA_VERSION)
    except Exception:
        pass
    ensure_search_fts_schema(con)


def _fts_columns(con) -> set[str]:
    if getattr(con, "dialect", "sqlite") == "postgresql":
        return _cols(con, "search_fts")
    try:
        return {r[1] for r in con.execute("PRAGMA table_info(search_fts)")}
    except Exception:
        return set()


def _init_pg_schema(con) -> None:
    schema_path = Path(__file__).resolve().parent / "schema_pg.sql"
    con.executescript(schema_path.read_text(encoding="utf-8"))


def ensure_pg_trgm_indexes(con) -> bool:
    """为 PG 全文检索建 pg_trgm GIN 索引。

    fts_pg 的召回是 `%term%` LIKE，普通 B-tree 用不上；trigram GIN 能让
    子串匹配走索引，避免语料变大后的全表扫描。失败（无权限/扩展不可用）
    只记日志，不影响主流程。
    """
    if getattr(con, "dialect", "sqlite") == "postgresql":
        pass
    else:
        return False
    try:
        con.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_fts_toks_trgm ON search_fts USING gin (toks gin_trgm_ops)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_fts_title_trgm ON search_fts USING gin (title gin_trgm_ops)"
        )
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_search_fts_body_trgm ON search_fts USING gin (body gin_trgm_ops)"
        )
        with write_lock():
            commit_retry(con)
        print("[mesh] pg_trgm indexes ensured", flush=True)
        return True
    except Exception as e:
        print(f"[mesh] pg_trgm index skipped: {e}", flush=True)
        try:
            con.rollback()
        except Exception:
            pass
        return False


def ensure_search_fts_schema(con) -> bool:
    """
    确保 search_fts 含 toks/date_end（中文 MATCH 用）。
    SQLite：旧四列结构重建 FTS5；PostgreSQL：普通表 + pg_trgm。
    """
    need = {"issue_slug", "section", "title", "body", "date_end", "toks"}
    cols = _fts_columns(con)
    if cols >= need:
        return False
    if getattr(con, "dialect", "sqlite") == "postgresql":
        # PG：不重建表，只确保 trigram 索引存在（让 %term% LIKE 能走索引）
        ensure_pg_trgm_indexes(con)
        return False
    con.execute("DROP TABLE IF EXISTS search_fts")
    con.execute("""CREATE VIRTUAL TABLE search_fts USING fts5(
      issue_slug UNINDEXED,
      section UNINDEXED,
      title UNINDEXED,
      body UNINDEXED,
      date_end UNINDEXED,
      toks,
      tokenize='unicode61'
    )""")
    try:
        con.execute(
            "INSERT INTO settings(key,value) VALUES('search_fts_needs_reindex','1') "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
        )
    except Exception:
        pass
    return True


def search_needs_reindex(con) -> bool:
    return (get_setting(con, "search_fts_needs_reindex") or "") == "1"


def clear_search_reindex_flag(con):
    try:
        set_setting(con, "search_fts_needs_reindex", "0")
    except Exception:
        pass


def init_db(seed: bool = True):
    con = connect()
    if is_postgres():
        _init_pg_schema(con)
    else:
        con.executescript(SCHEMA)
    migrate(con)
    # 默认管理员：必须用环境变量设密码；演示弱口令仅允许显式打开 MESH_ALLOW_DEMO_PASSWORDS=1
    import secrets as _secrets
    allow_demo = (os.environ.get("MESH_ALLOW_DEMO_PASSWORDS") or "").strip().lower() in ("1", "true", "yes")
    _WEAK_PW = {"mesh-admin", "mesh-dept", "mesh-viewer", "admin", "password", "123456", "owner"}
    admin_pw = (os.environ.get("MESH_ADMIN_PASSWORD") or "").strip()
    if not admin_pw:
        if allow_demo:
            admin_pw = "mesh-admin"
        else:
            admin_pw = _secrets.token_urlsafe(14)
            print(
                f"[mesh] 未设置 MESH_ADMIN_PASSWORD，已为首次建库生成临时密码（请立刻改掉）：{admin_pw}",
                flush=True,
            )
    elif admin_pw in _WEAK_PW and not allow_demo:
        print(
            "[mesh] 警告：MESH_ADMIN_PASSWORD 过弱，生产环境请换成强随机口令。",
            flush=True,
        )
    if not con.execute("SELECT 1 FROM users WHERE username='admin'").fetchone():
        con.execute("INSERT INTO users(username,display,pw_hash,role) VALUES(?,?,?,?)",
                    ("admin", "总裁办 · 管理员", hash_pw(admin_pw), "admin"))
        # 所有者：唯一有权确认上线的角色
        owner_pw = (os.environ.get("MESH_OWNER_PASSWORD") or "").strip() or admin_pw
        if owner_pw in _WEAK_PW and not allow_demo:
            print("[mesh] 警告：MESH_OWNER_PASSWORD 过弱。", flush=True)
        con.execute("INSERT INTO users(username,display,pw_hash,role) VALUES(?,?,?,?)",
                    ("owner", "山山 · 所有者", hash_pw(owner_pw), "owner"))
        # 演示账号仅在显式允许时创建，避免生产落弱口令账号
        if allow_demo:
            con.execute("INSERT INTO users(username,display,pw_hash,role,team) VALUES(?,?,?,?,?)",
                        ("biz", "商业化 · 编辑（演示）", hash_pw("mesh-dept"), "editor", "商业化团队"))
            con.execute("INSERT INTO users(username,display,pw_hash,role) VALUES(?,?,?,?)",
                        ("viewer", "普通同事（演示）", hash_pw("mesh-viewer"), "viewer"))
    # 种子期（v1.21 页面数据），保证部署后首页有内容
    if seed and not con.execute("SELECT 1 FROM issues").fetchone():
        seed_path = Path(__file__).resolve().parent / "seed_issue.json"
        if seed_path.exists():
            data = json.loads(seed_path.read_text(encoding="utf-8"))
            now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            con.execute("""INSERT INTO issues(slug,date_start,date_end,period_label,version,status,draft_json,published_json,updated_at,published_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (data["slug"], data["date_start"], data["date_end"], data["period_label"], data.get("version", "v1.4"),
                         "published", json.dumps(data, ensure_ascii=False), json.dumps(data, ensure_ascii=False),
                         data.get("updated_at", now), now))
            iid = con.execute("SELECT id FROM issues WHERE slug=?", (data["slug"],)).fetchone()["id"]
            reindex_issue(con, iid)
            register_entities(con, data, data["slug"])
    con.commit()
    con.close()

def get_setting(con, key, default=None):
    r = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default

def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))

# ---------- 搜索索引 ----------
# 团队名规范化：周报 JSON 里常写短名 / 箭头建议归属
_TEAM_ALIAS_DEFAULT = {
    "Global Partnership": "Global Partnership 团队",
    "GP": "Global Partnership 团队",
    "硅谷 BD": "硅谷 BD 团队",
    "硅谷BD": "硅谷 BD 团队",
    "硅谷": "硅谷 BD 团队",
    "硅谷团队": "硅谷 BD 团队",
    "硅谷的团队": "硅谷 BD 团队",
    "湾区": "硅谷 BD 团队",
    "Founder Park": "社群",
    "FounderPark": "社群",
    "Founder Park 团队": "社群",
    "Founder's Park": "社群",
    "FP": "社群",
    "内容侧": "编辑部",
    "编辑": "编辑部",
    "商业化": "商业化团队",
    "品牌": "品牌创意团队",
    "投资": "投资团队",
    "总裁办": "CEO / 总裁办",
    "播客": "音频播客团队",
    "播客团队": "音频播客团队",
    "视频号": "视频号团队",
    "GeekPark English": "英文站",
    "Geekpark English": "英文站",
    "极客公园英文站": "英文站",
    "GeekPark English站": "英文站",
}

def _team_alias_map() -> dict:
    m = dict(_TEAM_ALIAS_DEFAULT)
    # 飞书部门名 → Mesh 业务队（与 dept_team_map 同源）
    try:
        from .agent.dept_team_map import feishu_name_aliases

        m.update(feishu_name_aliases())
    except Exception:
        pass
    raw = (os.environ.get("MESH_TEAM_ALIASES") or "").strip()
    if raw:
        try:
            extra = json.loads(raw)
            if isinstance(extra, dict):
                m.update({str(k): str(v) for k, v in extra.items()})
        except Exception:
            pass
    return m

# 公开别名，供 qa_structured 等模块调用（勿依赖下划线私有名）
team_alias_map = _team_alias_map

def normalize_team(name: str) -> str | None:
    """把周报里的团队写法归一到 ingest.TEAMS；箭头建议归属（→…）不算已接触。"""
    from . import ingest
    t = (name or "").strip()
    if not t or t.startswith("→") or t.startswith("->"):
        return None
    # 「→ 社群 · 商业化团队」整段已在上面过滤；残留箭头片段再挡一次
    if "→" in t:
        return None
    aliases = _team_alias_map()
    if t in aliases:
        t = aliases[t]
    if t in ingest.TEAMS:
        return t
    # 缺「团队」后缀时补全再试
    if not t.endswith("团队") and (t + " 团队") in ingest.TEAMS:
        return t + " 团队"
    if not t.endswith("团队") and (t + "团队") in ingest.TEAMS:
        return t + "团队"
    # 在已知团队名中做包含匹配（长名优先）
    for canon in sorted(ingest.TEAMS, key=len, reverse=True):
        if canon in t or t in canon:
            return canon
    return None

def teams_from_blob(*parts: str) -> list[str]:
    """从标签/来源文案里抠出团队名。剥离「→ / ->」建议归属段，避免误记为已接触。"""
    from . import ingest
    blob = " ".join(p for p in parts if p)
    if not blob:
        return []
    blob = re.sub(r"(?:→|->)[^；;\n]*", " ", blob)
    found = []
    for canon in sorted(ingest.TEAMS, key=len, reverse=True):
        if canon in blob and canon not in found:
            found.append(canon)
    for alias, canon in _team_alias_map().items():
        if alias and alias in blob:
            n = normalize_team(canon) or canon
            if n and n not in found and n in ingest.TEAMS:
                found.append(n)
    return found

def _item_snippet(it: dict, limit: int = 240) -> str:
    bits = []
    if it.get("sub"):
        bits.append(str(it["sub"]))
    for row in it.get("rows") or []:
        k, v = row.get("k", ""), row.get("v", "")
        if k or v:
            bits.append(f"{k} {v}".strip())
    if it.get("cert"):
        bits.append(str(it["cert"]))
    return " · ".join(bits)[:limit]

def _infer_kind(section: str, name: str, sub: str = "") -> str:
    if section == "关注":
        return "topic"
    blob = f"{name} {sub}"
    if any(x in blob for x in ("创始人", "CEO", "CTO", "COO", "负责人", "合伙人", "·")):
        # 「公司名」条目的 sub 常带人名；有公司味关键词仍算公司
        if any(x in (name or "") for x in ("公司", "资本", "Capital", "科技", "智能", "集团", "实验室")):
            return "company"
        if "·" in (sub or "") or any(x in (sub or "") for x in ("创始人", "CEO", "负责人")):
            return "company"  # 接触卡：name 多为公司
    if section == "接触":
        return "company" if name else "unknown"
    return "unknown"

def _collect_item_teams(it: dict, parent_label: str = "", group_title: str = "") -> list[str]:
    """只采显式 teams[] 或「来源/归属」行；避免从正文 blob 误抠跨团队。"""
    teams = []
    for t in it.get("teams") or []:
        n = normalize_team(t)
        if n and n not in teams:
            teams.append(n)
    if teams:
        return teams
    src_bits = []
    for row in it.get("rows") or []:
        k = str(row.get("k") or "")
        if (
            k in ("来源", "来源团队", "归属", "归属团队")
            or "来源" in k
            or "归属" in k
        ):
            src_bits.append(str(row.get("v") or ""))
    return teams_from_blob(*src_bits)

def reindex_entity_facts(con, issue_id: int):
    """从已发布 JSON 物化 主体×团队×期号；仅 published 写入，草稿不进交叉语料。"""
    row = con.execute(
        "SELECT slug, status, date_start, date_end, published_json FROM issues WHERE id=?",
        (issue_id,),
    ).fetchone()
    if not row:
        return
    slug = row["slug"]
    con.execute("DELETE FROM entity_team_facts WHERE issue_slug=?", (slug,))
    if row["status"] != "published" or not (row["published_json"] or "").strip():
        return
    try:
        data = json.loads(row["published_json"])
    except Exception:
        return
    date_start, date_end = row["date_start"], row["date_end"]

    def add(section, name, team, kind, group_title, snippet, source_hint):
        name = (name or "").strip()
        if not name or not team:
            return
        con.execute(
            """INSERT INTO entity_team_facts
               (issue_slug, date_start, date_end, section, name, team, kind, group_title, snippet, source_hint)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (slug, date_start, date_end, section, name, team, kind or "unknown",
             group_title or "", (snippet or "")[:500], source_hint or ""),
        )

    for r in data.get("relations") or []:
        title = r.get("title") or ""
        snippet = " ".join(
            [r.get("label", ""), r.get("body", "")] + (r.get("details") or []) + (r.get("sources") or [])
        )[:500]
        src = "；".join(r.get("sources") or [])[:200]
        for t in r.get("teams") or []:
            team = normalize_team(t)
            if not team:
                continue
            add("关系", title, team, "company", r.get("label") or "", snippet, src)

    for c in data.get("contacts") or []:
        parent = " ".join(filter(None, [c.get("label"), c.get("title")]))
        for g in c.get("groups") or []:
            gtitle = g.get("title") or ""
            for it in g.get("items") or []:
                name = it.get("name") or ""
                teams = _collect_item_teams(it, parent, gtitle)
                sn = _item_snippet(it)
                src = ""
                for row_ in it.get("rows") or []:
                    if "来源" in str(row_.get("k") or ""):
                        src = str(row_.get("v") or ""); break
                kind = _infer_kind("接触", name, it.get("sub") or "")
                for team in teams:
                    add("接触", name, team, kind, gtitle, sn, src)

    for g in (data.get("keywords") or {}).get("groups") or []:
        gtitle = g.get("title") or ""
        for it in g.get("items") or []:
            name = it.get("name") or ""
            teams = _collect_item_teams(it, "关注了什么", gtitle)
            sn = _item_snippet(it)
            src = ""
            for row_ in it.get("rows") or []:
                if "来源" in str(row_.get("k") or ""):
                    src = str(row_.get("v") or ""); break
            for team in teams:
                add("关注", name, team, "topic", gtitle, sn, src)

    for v in data.get("views") or []:
        topic = v.get("topic") or ""
        text = (v.get("text") or "") + " " + (v.get("source") or "")
        # 仅从来源字段抠团队，避免看法正文里的提及被物化为「主体×团队」
        teams = teams_from_blob(v.get("source") or "")
        if not teams:
            continue
        for team in teams:
            add("看法", topic or "看法", team, "topic", "", text[:500], v.get("source") or "")

    for ds in data.get("data_sources") or []:
        tname = normalize_team(ds.get("team") or "") or (ds.get("team") or "").strip()
        if not tname:
            continue
        add("贡献", tname, tname, "team", "Data Source", (ds.get("text") or "")[:500], "")


def reindex_all_entity_facts(con) -> int:
    """回填所有已发布期的事实表。返回处理期数。"""
    ids = [r["id"] for r in con.execute("SELECT id FROM issues WHERE status='published'")]
    for iid in ids:
        reindex_entity_facts(con, iid)
    return len(ids)


def reindex_issue(con, issue_id: int, *, items: bool = True, rebuild_chunks: bool = True):
    """仅已上线期进入搜索语料；草稿/未上线先清索引，避免问答/搜索泄露。
    items=False 时只重建 published_json 层（FTS/entity），不写 live items 到 item_facts。
    rebuild_chunks=False 时跳过 chunk_index（Publish 可异步补建）。
    """
    from . import tokenize as tok
    row = con.execute(
        "SELECT slug, status, date_end, published_json FROM issues WHERE id=?",
        (issue_id,),
    ).fetchone()
    if not row:
        return
    slug = row["slug"]
    con.execute("DELETE FROM search_fts WHERE issue_slug=?", (slug,))
    if row["status"] != "published" or not (row["published_json"] or "").strip():
        from . import item_facts, chunk_index
        item_facts.reindex_item_facts(con, issue_id)
        reindex_entity_facts(con, issue_id)
        if rebuild_chunks:
            chunk_index.rebuild_issue(con, issue_id, items=False)
        return
    date_end = row["date_end"] or ""
    try:
        data = json.loads(row["published_json"])
    except Exception:
        data = {}

    def add(section, title, body):
        title = title or ""
        body = body or ""
        toks = tok.tokenize_for_index(f"{title} {body}")
        con.execute(
            "INSERT INTO search_fts(issue_slug,section,title,body,date_end,toks) VALUES(?,?,?,?,?,?)",
            (slug, section, title, body, date_end, toks),
        )

    for r in data.get("relations", []):
        add("可同步的关系", r.get("title"), " ".join([r.get("label",""), r.get("body","")] + r.get("details", []) + r.get("sources", []) + r.get("teams", [])))
    for c in data.get("contacts", []):
        for g in c.get("groups", []):
            for it in g.get("items", []):
                add("接触过的人和公司", it.get("name"), " ".join([it.get("sub",""), g.get("title","")] + [f"{x.get('k')} {x.get('v')}" for x in it.get("rows", [])]))
    for sec_key, sec_name in (("keywords", "关注了什么"), ("plans", "日程与计划")):
        for g in data.get(sec_key, {}).get("groups", []):
            for it in g.get("items", []):
                add(sec_name, it.get("name"), " ".join([it.get("sub",""), it.get("cert",""), g.get("title","")] + [f"{x.get('k')} {x.get('v')}" for x in it.get("rows", [])]))
    for v in data.get("views", []): add("沟通中提到的看法", v.get("topic"), (v.get("text","") + " " + v.get("source","")))
    for g in data.get("gaps", []): add("本期未汇入", g.get("topic"), g.get("text"))
    for ds in data.get("data_sources") or []:
        add("Data Source", ds.get("team"), ds.get("text") or "")
    from . import item_facts, chunk_index
    if items:
        item_facts.reindex_item_facts(con, issue_id)
    else:
        slug = row["slug"]
        con.execute("DELETE FROM item_facts WHERE issue_slug=?", (slug,))
        con.execute("DELETE FROM item_entity_facts WHERE issue_slug=?", (slug,))
        item_facts.sync_fts(con)
    reindex_entity_facts(con, issue_id)
    if rebuild_chunks:
        chunk_index.rebuild_issue(con, issue_id, items=items)
        refresh_issue_embedding_status(con, issue_id)


def refresh_issue_embedding_status(
    con,
    issue_id: int,
    *,
    status: str | None = None,
    error: str | None = None,
    running: bool = False,
) -> dict:
    """按 chunk_index / chunk_embeddings（当前 model）刷新该期 embedding 进度。"""
    from . import chunk_index, embeddings

    row = con.execute(
        "SELECT slug, status, embedding_model FROM issues WHERE id=?", (issue_id,),
    ).fetchone()
    if not row:
        return {}
    slug = row["slug"]
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    model = embeddings.model_name() if embeddings.is_configured() else ""
    if row["status"] != "published":
        con.execute(
            "UPDATE issues SET embedding_status='', embedding_total=0, embedding_done=0, "
            "embedding_model='', embedding_at=NULL, embedding_error=NULL WHERE id=?",
            (issue_id,),
        )
        return {"slug": slug, "embedding_status": "", "embedding_model": ""}
    if not embeddings.is_configured():
        con.execute(
            "UPDATE issues SET embedding_status='skipped', embedding_total=0, embedding_done=0, "
            "embedding_model='', embedding_at=?, embedding_error=NULL WHERE id=?",
            (now, issue_id),
        )
        return {"slug": slug, "embedding_status": "skipped", "embedding_model": ""}
    total, done = chunk_index.embedding_counts_for_slug(con, slug, model=model)
    stored_model = (row["embedding_model"] or "").strip()
    err = (error or "").strip() or None
    if status:
        st = status
    elif running:
        st = "running"
    elif total <= 0:
        st = "completed"
    elif done >= total:
        st = "completed"
    elif stored_model and stored_model != model:
        # 换模型后：旧 completed 不能沿用，按当前 model 缺口重算
        st = "partial" if done > 0 else "pending"
    elif err and done <= 0:
        st = "failed"
    elif done > 0:
        st = "partial"
    else:
        st = "pending"
    record_model = model if st in ("completed", "running", "partial", "pending", "failed") else stored_model
    if st == "completed":
        record_model = model
    con.execute(
        "UPDATE issues SET embedding_status=?, embedding_total=?, embedding_done=?, "
        "embedding_model=?, embedding_at=?, embedding_error=? WHERE id=?",
        (st, total, done, record_model, now, err, issue_id),
    )
    return {
        "slug": slug,
        "embedding_status": st,
        "embedding_total": total,
        "embedding_done": done,
        "embedding_model": record_model,
        "embedding_error": err,
    }


def issues_needing_embedding(con) -> list[str]:
    """已发布且向量未齐的期号（供启动回填 / 运维）。"""
    from . import embeddings

    if not embeddings.is_configured():
        return []
    out: list[str] = []
    for r in con.execute(
        "SELECT id, slug FROM issues WHERE status='published' ORDER BY date_end DESC"
    ):
        slug = r["slug"]
        if not slug:
            continue
        info = refresh_issue_embedding_status(con, r["id"])
        if info.get("embedding_status") in ("pending", "partial", "failed"):
            out.append(slug)
    return out


def reindex_all_search(con) -> int:
    """全量重建 FTS + 事实表。返回处理期数（含清掉未上线索引）。"""
    ids = [r["id"] for r in con.execute("SELECT id FROM issues")]
    for iid in ids:
        reindex_issue(con, iid)
    return len(ids)


def clear_issue_search(con, slug: str):
    con.execute("DELETE FROM search_fts WHERE issue_slug=?", (slug,))
    con.execute("DELETE FROM entity_team_facts WHERE issue_slug=?", (slug,))
    con.execute("DELETE FROM item_facts WHERE issue_slug=?", (slug,))
    con.execute("DELETE FROM item_entity_facts WHERE issue_slug=?", (slug,))
    con.execute("DELETE FROM chunk_index WHERE issue_slug=?", (slug,))
    try:
        con.execute(
            "DELETE FROM chunk_embeddings WHERE chunk_id NOT IN (SELECT chunk_id FROM chunk_index)"
        )
    except Exception:
        pass
    try:
        from . import item_facts
        item_facts.sync_fts(con)
    except Exception:
        pass


def delete_issue(con, slug: str) -> bool:
    """删除一期及其素材、条目、卡片、审计、邮件、版本与搜索索引。"""
    r = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not r:
        return False
    iid = r["id"]
    clear_issue_search(con, slug)
    con.execute("DELETE FROM items WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM sources WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM cards WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM edits WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM mail_log WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM versions WHERE issue_id=?", (iid,))
    con.execute("DELETE FROM issues WHERE id=?", (iid,))
    return True


def issue_json_renderable(data_or_json) -> bool:
    """预览/读者页能否按 issue.html 结构渲染（空 {} 不行）。"""
    if isinstance(data_or_json, str) or data_or_json is None:
        if not (data_or_json or "").strip():
            return False
        try:
            data = json.loads(data_or_json)
        except Exception:
            return False
    else:
        data = data_or_json
    if not isinstance(data, dict) or not data:
        return False
    return any(k in data for k in ("question", "relations", "contacts", "keywords", "plans", "kpis"))


def draft_is_ready(draft_json: str | None) -> bool:
    """草稿可预览/可上线：有内容结构，且未被标为过期。"""
    if not issue_json_renderable(draft_json):
        return False
    try:
        data = json.loads(draft_json)
    except Exception:
        return True
    return not bool(data.get("_stale"))


def draft_teams_for_issue(con, issue_id: int) -> list[str]:
    """有效条目归属团队（生成草稿 / 索引）。"""
    from .aggregator import INVALID_OWNER_TEAMS
    invalid = tuple(INVALID_OWNER_TEAMS)
    ph = ",".join("?" * len(invalid)) if invalid else "''"
    rows = con.execute(
        f"""SELECT DISTINCT owner_team FROM items WHERE issue_id=? AND blocked=0
            AND merged_into IS NULL AND owner_team IS NOT NULL AND owner_team <> ''
            AND owner_team NOT IN ({ph})""",
        (issue_id, *invalid),
    )
    return [x["owner_team"] for x in rows if (x["owner_team"] or "") not in ("外部媒体",)]


def get_feishu_chat_team(con, chat_id: str) -> str | None:
    """飞书群绑定团队；无绑定返回 None（全公司视角）。"""
    if not chat_id:
        return None
    row = con.execute("SELECT team FROM feishu_chat_bindings WHERE chat_id=?", (chat_id,)).fetchone()
    if not row:
        return None
    t = (row["team"] or "").strip()
    return normalize_team(t) or t or None


def snapshot_published_items(con, issue_id: int) -> int:
    """上线或重建索引时冻结条目快照，供 item_facts 与 live items 解耦。"""
    rows = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, team, stype, zone, level, kind, text, entities, roles, signals,
               source_label, pointer, blocked, owner_team, channel, merged_into, source_labels
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    con.execute(
        "UPDATE issues SET published_items_snapshot=? WHERE id=?",
        (json.dumps(rows, ensure_ascii=False), issue_id),
    )
    return len(rows)


def iter_index_items(con, issue_id: int):
    """已上线期优先读 publish 快照；否则读 live items（抽取/草稿阶段）。"""
    row = con.execute(
        "SELECT status, published_items_snapshot FROM issues WHERE id=?", (issue_id,)
    ).fetchone()
    if row and row["status"] == "published" and (row["published_items_snapshot"] or "").strip():
        try:
            for it in json.loads(row["published_items_snapshot"]):
                yield it
            return
        except Exception:
            _log.warning("published_items_snapshot corrupt for issue_id=%s", issue_id)
    for it in con.execute(
        """SELECT id, source_id, team, stype, zone, level, kind, text, entities, roles, signals,
           source_label, pointer, blocked, owner_team, channel, merged_into, source_labels
           FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
        (issue_id,),
    ):
        yield dict(it)


def prune_old_logs(con, *, ask_days: int = 180, push_days: int = 90) -> dict:
    """裁剪问答/推送日志，避免无限增长（SQLite / PostgreSQL 通用）。"""
    import datetime as _dt

    def _cutoff(days: int) -> str:
        return (_dt.datetime.now() - _dt.timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")

    ask_cut = _cutoff(ask_days)
    push_cut = _cutoff(push_days)
    out = {"ask_log": 0, "ask_messages": 0, "preset_push_log": 0}
    for table, col, cutoff, key in (
        ("ask_log", "created_at", ask_cut, "ask_log"),
        ("preset_push_log", "pushed_at", push_cut, "preset_push_log"),
    ):
        try:
            cur = con.execute(f"DELETE FROM {table} WHERE {col} < ?", (cutoff,))
            out[key] = int(getattr(cur, "rowcount", 0) or 0)
        except Exception:
            pass
    try:
        cur = con.execute(
            """DELETE FROM ask_messages WHERE session_id IN (
               SELECT id FROM ask_sessions WHERE last_active_at < ?
            )""",
            (ask_cut,),
        )
        out["ask_messages"] = int(getattr(cur, "rowcount", 0) or 0)
        con.execute("DELETE FROM ask_sessions WHERE last_active_at < ?", (ask_cut,))
    except Exception:
        pass
    return out


# 挖掘或重生成卡片后，强制要求重新「生成周报草稿」。
# 渐进预览骨架期间：若仍在 building，不打 _stale，避免进页瞬间被 draft_is_ready 判死。
def mark_draft_stale(con, issue_id: int) -> None:
    """挖掘或重生成卡片后，强制要求重新「生成周报草稿」。"""
    row = con.execute("SELECT draft_json FROM issues WHERE id=?", (issue_id,)).fetchone()
    if not row or not row["draft_json"]:
        return
    try:
        data = json.loads(row["draft_json"])
    except Exception:
        return
    if data.get("_preview_building") or data.get("_preview_partial_ready"):
        return
    if data.get("_stale"):
        return
    data["_stale"] = True
    con.execute("UPDATE issues SET draft_json=? WHERE id=?", (json.dumps(data, ensure_ascii=False), issue_id))

# ---------- 主体登记（用于"首次进入记录"）----------
def register_entities(con, data: dict, slug: str):
    names = set()
    for r in data.get("relations", []): names.add(r.get("title", ""))
    for c in data.get("contacts", []):
        for g in c.get("groups", []):
            for it in g.get("items", []): names.add(it.get("name", ""))
    for g in data.get("keywords", {}).get("groups", []):
        for it in g.get("items", []): names.add(it.get("name", ""))
    for n in names:
        n = (n or "").strip()
        if not n: continue
        con.execute("INSERT INTO entities(name,kind,first_issue) VALUES(?,?,?) ON CONFLICT(name) DO NOTHING", (n, "auto", slug))

def first_appearances(con, data: dict, slug: str) -> list[str]:
    """返回本期首次出现（此前归档中没有）的主体名"""
    names = []
    for c in data.get("contacts", []):
        for g in c.get("groups", []):
            for it in g.get("items", []):
                n = (it.get("name") or "").strip()
                if not n: continue
                r = con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone()
                if not r or r["first_issue"] == slug: names.append(n)
    seen, out = set(), []
    for n in names:
        if n not in seen: seen.add(n); out.append(n)
    return out
