"""问答编排：范围 + 多路检索 + 会话 + 审计。"""
from __future__ import annotations

import time
from typing import Any

from . import qa_structured, retriever
from .ask_scope import AskScope
from .ask_query import retrieval_query


def prepare(con, q: str, scope: AskScope, history: list[dict] | None = None) -> dict:
    """检索并组装 LLM 上下文（不调模型）。"""
    t0 = time.time()
    q = (q or "").strip()
    if not q:
        return {
            "mode": "hybrid", "n_context": 0, "contexts": [], "direct_answer": None,
            "scope": scope.scope_key, "n_hits": 0,
        }

    search_q = retrieval_query(q, history or [])
    intent = qa_structured.parse_intent(search_q)
    if intent and intent.get("type") == "error":
        return {
            "mode": "structured", "n_context": 0, "contexts": [],
            "direct_answer": intent.get("message") or "无法解析该交叉问题",
            "scope": scope.scope_key, "latency_ms": 0, "n_hits": 0,
        }

    mode, hits, meta = retriever.retrieve(con, search_q, scope, intent=intent)
    latency = int((time.time() - t0) * 1000)

    if mode == "structured":
        st = meta.get("structured") or {}
        if not st.get("ok"):
            return {
                "mode": "structured", "n_context": 0, "contexts": [],
                "direct_answer": st.get("message") or "结构化检索失败",
                "scope": scope.scope_key, "latency_ms": latency, "n_hits": 0,
            }
        ctxs = st["contexts"]
        intent_out = st.get("intent") or {}
        return {
            "mode": "structured",
            "n_context": max(0, len(ctxs) - 1),
            "n_hits": st.get("total", 0),
            "total": st.get("total", 0),
            "query": intent_out,
            "date_from": intent_out.get("date_from"),
            "date_to": intent_out.get("date_to"),
            "contexts": ctxs,
            "direct_answer": None,
            "scope": scope.scope_key,
            "team_scope": scope.team_filter,
            "latency_ms": latency,
        }

    ctxs = retriever.hits_to_contexts(hits, q)
    n_ctx = len(ctxs)
    if meta.get("date_from") or meta.get("date_to"):
        n_ctx = max(0, n_ctx - 1)
    report_mode = "hybrid" if meta.get("used_vector") else "lexical"
    return {
        "mode": report_mode,
        "n_context": n_ctx,
        "n_hits": len(hits),
        "date_from": meta.get("date_from"),
        "date_to": meta.get("date_to"),
        "contexts": ctxs,
        "direct_answer": None,
        "scope": scope.scope_key,
        "team_scope": scope.team_filter,
        "latency_ms": latency,
    }


def log_ask(
    con,
    scope: AskScope,
    user: dict | None,
    q: str,
    prepared: dict,
    session_id: str = "",
) -> None:
    u = user or {}
    con.execute(
        """INSERT INTO ask_log(session_id, user_id, feishu_open_id, chat_id, thread_id,
           team_scope, query, mode, n_hits, latency_ms)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (
            session_id or None,
            u.get("id"),
            scope.feishu_open_id or u.get("feishu_open_id"),
            scope.chat_id or None,
            scope.thread_id or None,
            scope.team_filter or None,
            q,
            prepared.get("mode"),
            prepared.get("n_hits", prepared.get("n_context")),
            prepared.get("latency_ms"),
        ),
    )
