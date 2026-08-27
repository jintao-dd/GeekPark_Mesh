"""独立会话上下文：每人 / 每群 / 每话题线程。"""
from __future__ import annotations

import json
import secrets
import datetime
from typing import Any

from . import db
from .ask_scope import AskScope


def ensure_session(con, scope: AskScope, user: dict | None = None) -> dict:
    """获取或创建隔离会话。"""
    key = scope.scope_key
    row = con.execute("SELECT * FROM ask_sessions WHERE scope_key=?", (key,)).fetchone()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    u = user or {}
    if row:
        con.execute(
            "UPDATE ask_sessions SET last_active_at=?, updated_at=? WHERE id=?",
            (now, now, row["id"]),
        )
        return dict(row)
    sid = secrets.token_hex(12)
    title = (scope.channel == "feishu_group" and f"群聊 {scope.chat_id[:8]}") or "问答"
    try:
        con.execute(
            """INSERT INTO ask_sessions(
               id, scope_key, channel, user_id, feishu_open_id, chat_id, thread_id,
               team_scope, title, meta_json, created_at, updated_at, last_active_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                sid, key, scope.channel, u.get("id"), scope.feishu_open_id or u.get("feishu_open_id") or None,
                scope.chat_id or None, scope.thread_id or None, scope.team_filter or None,
                title, json.dumps({"role": scope.role}, ensure_ascii=False),
                now, now, now,
            ),
        )
    except db.IntegrityError:
        row = con.execute("SELECT * FROM ask_sessions WHERE scope_key=?", (key,)).fetchone()
        if row:
            return dict(row)
        raise
    row = con.execute("SELECT * FROM ask_sessions WHERE id=?", (sid,)).fetchone()
    return dict(row)


def recent_messages(con, session_id: str, limit: int = 8) -> list[dict]:
    rows = con.execute(
        "SELECT role, content FROM ask_messages WHERE session_id=? ORDER BY id DESC LIMIT ?",
        (session_id, limit),
    ).fetchall()
    return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]


def append_turn(
    con,
    session_id: str,
    *,
    user_text: str,
    assistant_text: str,
    mode: str = "",
    n_context: int = 0,
    meta: dict | None = None,
) -> None:
    meta_json = json.dumps(meta or {}, ensure_ascii=False)
    con.execute(
        "INSERT INTO ask_messages(session_id, role, content, mode, n_context, meta_json) VALUES (?,?,?,?,?,?)",
        (session_id, "user", user_text, mode, n_context, meta_json),
    )
    con.execute(
        "INSERT INTO ask_messages(session_id, role, content, mode, n_context, meta_json) VALUES (?,?,?,?,?,?)",
        (session_id, "assistant", assistant_text, mode, n_context, meta_json),
    )
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute(
        "UPDATE ask_sessions SET last_active_at=?, updated_at=? WHERE id=?",
        (now, now, session_id),
    )


def list_sessions(con, user_id: int, limit: int = 20) -> list[dict]:
    rows = con.execute(
        """SELECT id, scope_key, channel, title, team_scope, last_active_at
           FROM ask_sessions WHERE user_id=? ORDER BY last_active_at DESC LIMIT ?""",
        (user_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]
