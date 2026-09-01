#!/usr/bin/env python3
"""SQLite → PostgreSQL 一次性迁移。

用法（在 mesh/ 目录）:
  export MESH_DB=/path/to/mesh.db
  export MESH_DB_URL=postgresql://mesh:mesh@localhost:5432/mesh
  python deploy/migrate_to_postgres.py

迁移完成后：在 .env 设置 MESH_DB_URL，重启服务；可保留 mesh.db 作备份。
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, db_conn  # noqa: E402

# 表顺序：先主表，后索引/日志
_TABLES = [
    ("users", ["id", "username", "display", "pw_hash", "role", "team", "feishu_open_id", "avatar_url", "created_at"]),
    ("issues", ["id", "slug", "date_start", "date_end", "period_label", "version", "status",
                "draft_json", "published_json", "published_items_snapshot", "updated_at", "published_at", "created_at"]),
    ("sources", ["id", "issue_id", "stype", "team", "title", "filename", "raw_path", "text", "meta",
                 "fetched_at", "extracted", "channel"]),
    ("items", ["id", "issue_id", "source_id", "team", "stype", "zone", "level", "kind", "text",
               "entities", "roles", "signals", "source_label", "pointer", "blocked", "owner_team",
               "channel", "merged_into", "source_labels", "created_at"]),
    ("entities", ["id", "name", "kind", "roles", "first_issue", "listed", "aliases"]),
    ("cards", ["id", "issue_id", "team", "card_json", "status", "reviewer", "reviewed_at", "created_at"]),
    ("edits", ["id", "issue_id", "user", "target", "before", "after", "at"]),
    ("mail_log", ["id", "issue_id", "to_addr", "subject", "ok", "error", "at"]),
    ("settings", ["key", "value"]),
    ("versions", ["id", "issue_id", "version", "at", "by_user", "cards_out", "edited_count", "url", "snapshot_json"]),
    ("search_fts", ["issue_slug", "section", "title", "body", "date_end", "toks"]),
    ("entity_team_facts", ["id", "issue_slug", "date_start", "date_end", "section", "name", "team",
                           "kind", "group_title", "snippet", "source_hint"]),
    ("item_facts", ["id", "issue_slug", "issue_id", "date_start", "date_end", "item_id", "source_id",
                    "owner_team", "stype", "zone", "level", "kind", "primary_name", "entities_json",
                    "roles_json", "signals_json", "text_snippet", "source_label", "channel", "toks", "meta_json"]),
    ("item_facts_fts", ["issue_slug", "date_end", "item_id", "source_id", "owner_team", "stype",
                        "primary_name", "text_snippet", "source_label", "level", "kind", "zone", "toks"]),
    ("item_entity_facts", ["id", "issue_slug", "issue_id", "date_start", "date_end", "item_id", "source_id",
                           "owner_team", "stype", "entity_name", "entity_kind", "text_snippet", "source_label", "channel"]),
    ("feishu_chat_bindings", ["chat_id", "chat_type", "team", "label", "created_at", "updated_at"]),
    ("chunk_index", ["chunk_id", "issue_slug", "issue_id", "date_end", "layer", "section", "stype",
                     "owner_team", "entity_name", "title", "body", "source_label", "item_id", "source_id", "toks", "meta_json"]),
    ("chunk_embeddings", ["chunk_id", "model", "dim", "vector_json", "updated_at"]),
    ("ask_sessions", ["id", "scope_key", "channel", "user_id", "feishu_open_id", "chat_id", "thread_id",
                      "team_scope", "title", "meta_json", "created_at", "updated_at", "last_active_at"]),
    ("ask_messages", ["id", "session_id", "role", "content", "mode", "n_context", "meta_json", "created_at"]),
    ("ask_log", ["id", "session_id", "user_id", "feishu_open_id", "chat_id", "thread_id", "team_scope",
                 "query", "mode", "n_hits", "latency_ms", "created_at"]),
    ("user_ask_presets", ["id", "scope", "user_id", "target_team", "title", "question_template", "schedule",
                          "schedule_time", "schedule_dow", "team_scope", "enabled", "created_by", "last_pushed_at",
                          "created_at", "updated_at"]),
    ("preset_push_log", ["id", "preset_id", "user_id", "answer_snippet", "ok", "error", "pushed_at"]),
]

_SERIAL_TABLES = {
    "users", "issues", "sources", "items", "entities", "cards", "edits", "mail_log", "versions",
    "entity_team_facts", "item_facts", "item_entity_facts", "ask_messages", "ask_log",
    "user_ask_presets", "preset_push_log", "search_fts", "item_facts_fts",
}


def _sqlite_path() -> str:
    return os.environ.get("MESH_DB", str(ROOT / "data" / "mesh.db"))


def _copy_table(src: sqlite3.Connection, dst, table: str, columns: list[str]) -> int:
    try:
        rows = src.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
    except sqlite3.OperationalError as e:
        print(f"  skip {table}: {e}")
        return 0
    if not rows:
        return 0
    placeholders = ", ".join(["%s"] * len(columns))
    col_sql = ", ".join(f'"{c}"' if c == "user" else c for c in columns)
    sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT DO NOTHING"
    if table == "settings":
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value"
    elif table in ("users", "issues", "entities", "ask_sessions", "chunk_index", "chunk_embeddings", "feishu_chat_bindings"):
        conflict = columns[0]
        sql = f"INSERT INTO {table} ({col_sql}) VALUES ({placeholders}) ON CONFLICT ({conflict}) DO NOTHING"
    n = 0
    for row in rows:
        dst.execute(sql, tuple(row))
        n += 1
    return n


def _reset_serial(dst, table: str) -> None:
    if table not in _SERIAL_TABLES:
        return
    try:
        dst.execute(
            "SELECT setval(pg_get_serial_sequence(%s, 'id'), COALESCE((SELECT MAX(id) FROM " + table + "), 1), true)",
            (table,),
        )
    except Exception:
        pass


def _pg_has_data(dst) -> bool:
    try:
        row = dst.execute("SELECT COUNT(*) c FROM issues").fetchone()
        return bool(row and row["c"] > 0)
    except Exception:
        return False


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="SQLite → PostgreSQL 全量迁移")
    ap.add_argument(
        "--force",
        action="store_true",
        help="目标 PG 已有数据时仍 TRUNCATE 后覆盖（危险，仅首次或明确恢复时用）",
    )
    args = ap.parse_args()

    if not db_conn.MESH_DB_URL:
        print("错误：请设置 MESH_DB_URL=postgresql://...", file=sys.stderr)
        sys.exit(1)
    src_path = _sqlite_path()
    if not Path(src_path).exists():
        print(f"错误：SQLite 不存在: {src_path}", file=sys.stderr)
        sys.exit(1)

    print(f"==> 源 SQLite: {src_path}")
    print(f"==> 目标 PG:   {db_conn.MESH_DB_URL.split('@')[-1]}")

    src = sqlite3.connect(src_path)
    src.row_factory = sqlite3.Row

    dst = db.connect()
    assert dst.dialect == "postgresql"
    db._init_pg_schema(dst)
    if _pg_has_data(dst) and not args.force:
        print(
            "错误：目标 PostgreSQL 已有期号数据。若确需全量覆盖请加 --force",
            file=sys.stderr,
        )
        dst.close()
        src.close()
        sys.exit(2)
    if args.force and _pg_has_data(dst):
        print("==> --force：清空目标 PG 表…")
    for table, _ in reversed(_TABLES):
        try:
            dst.execute(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE")
        except Exception:
            pass
    dst.commit()

    total = 0
    for table, cols in _TABLES:
        n = _copy_table(src, dst, table, cols)
        dst.commit()
        if table in _SERIAL_TABLES:
            _reset_serial(dst, table)
            dst.commit()
        print(f"  {table}: {n} rows")
        total += n

    db.set_setting(dst, "schema_version", db.SCHEMA_VERSION)
    db.set_setting(dst, "migrated_from_sqlite", src_path)
    dst.commit()
    dst.close()
    src.close()
    print(f"==> 完成，共复制 {total} 行")


if __name__ == "__main__":
    main()
