"""Ask 分析结果持久化（按 analysis_id）。

为追问 context_refs、审计回放、后续「SSE 断 ≠ 取消」打底。
"""
from __future__ import annotations

import json
import datetime
from typing import Any


def ensure_table(con) -> None:
    con.execute(
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
           finished_at TEXT
        )"""
    )
    try:
        con.execute(
            "CREATE INDEX IF NOT EXISTS idx_ask_analyses_sess ON ask_analyses(session_id, created_at)"
        )
    except Exception:
        pass


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def start(
    con,
    *,
    analysis_id: str,
    question: str,
    session_id: str = "",
    user_id: int | None = None,
    parent_analysis_id: str | None = None,
) -> None:
    ensure_table(con)
    now = _now()
    con.execute(
        """INSERT INTO ask_analyses(
             analysis_id, parent_analysis_id, session_id, user_id, question,
             status, created_at, updated_at)
           VALUES (?,?,?,?,?,'running',?,?)
           ON CONFLICT(analysis_id) DO UPDATE SET
             question=excluded.question, updated_at=excluded.updated_at, status='running'""",
        (
            analysis_id,
            parent_analysis_id,
            session_id or None,
            user_id,
            (question or "")[:2000],
            now,
            now,
        ),
    )


def finish(
    con,
    *,
    analysis_id: str,
    answer: str = "",
    context_refs: dict | None = None,
    usage: dict | None = None,
    verify: dict | None = None,
    sources: list | None = None,
    status: str = "completed",
    question: str = "",
    session_id: str = "",
    user_id: int | None = None,
    parent_analysis_id: str | None = None,
) -> None:
    ensure_table(con)
    now = _now()
    refs_j = json.dumps(context_refs or {}, ensure_ascii=False)
    usage_j = json.dumps(usage or {}, ensure_ascii=False)
    verify_j = json.dumps(verify or {}, ensure_ascii=False)
    sources_j = json.dumps(sources or [], ensure_ascii=False)
    existing = con.execute(
        "SELECT analysis_id FROM ask_analyses WHERE analysis_id=?", (analysis_id,)
    ).fetchone()
    if existing:
        con.execute(
            """UPDATE ask_analyses SET
                 status=?, answer=?, context_refs_json=?, usage_json=?, verify_json=?,
                 sources_json=?, updated_at=?, finished_at=?
               WHERE analysis_id=?""",
            (
                status or "completed",
                (answer or "")[:50000],
                refs_j,
                usage_j,
                verify_j,
                sources_j,
                now,
                now,
                analysis_id,
            ),
        )
    else:
        con.execute(
            """INSERT INTO ask_analyses(
                 analysis_id, parent_analysis_id, session_id, user_id, question,
                 status, answer, context_refs_json, usage_json, verify_json, sources_json,
                 created_at, updated_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                analysis_id,
                parent_analysis_id,
                session_id or None,
                user_id,
                (question or "")[:2000],
                status or "completed",
                (answer or "")[:50000],
                refs_j,
                usage_j,
                verify_j,
                sources_j,
                now,
                now,
                now,
            ),
        )


def get(con, analysis_id: str) -> dict[str, Any] | None:
    ensure_table(con)
    row = con.execute(
        "SELECT * FROM ask_analyses WHERE analysis_id=?", (analysis_id,)
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    for k, jk in (
        ("context_refs", "context_refs_json"),
        ("usage", "usage_json"),
        ("verify", "verify_json"),
        ("sources", "sources_json"),
    ):
        raw = d.pop(jk, None)
        try:
            d[k] = json.loads(raw) if raw else ({} if k != "sources" else [])
        except Exception:
            d[k] = {} if k != "sources" else []
    return d
