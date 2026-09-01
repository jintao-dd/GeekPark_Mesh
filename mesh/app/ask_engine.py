"""问答编排：范围 + 多路检索 + 会话 + 审计。"""
from __future__ import annotations

import time
from typing import Any

from . import qa_structured, retriever, embeddings
from .ask_scope import AskScope
from .ask_query import retrieval_query
from .ask_query_guard import guard_direct_answer
from .ask_planner import CONFIDENCE_THRESHOLD, plan_retrieval

_NO_EVIDENCE_ANSWER = (
    "未在已上线周报中找到与问题直接相关的记录。"
    "请尝试换关键词、缩小或扩大时间范围，或确认相关内容是否已上线。"
)


def _lexical_no_evidence_answer(meta: dict) -> str:
    df, dt = meta.get("date_from"), meta.get("date_to")
    if df or dt:
        return (
            f"{_NO_EVIDENCE_ANSWER}\n"
            f"（本次检索时间范围：{df or '…'} 至 {dt or '…'}）"
        )
    return _NO_EVIDENCE_ANSWER


def prepare(
    con,
    q: str,
    scope: AskScope,
    history: list[dict] | None = None,
    *,
    search_q: str | None = None,
    context_refs: dict | None = None,
) -> dict:
    """检索并组装 LLM 上下文（不调模型）。

    history 参数保留兼容，但不再用于成文；追问检索靠 search_q / context_refs。
    """
    t0 = time.time()
    q = (q or "").strip()
    if not q:
        return {
            "mode": "hybrid", "n_context": 0, "contexts": [], "direct_answer": None,
            "scope": scope.scope_key, "n_hits": 0,
        }

    guarded = guard_direct_answer(q)
    if guarded:
        return {
            "mode": "guard",
            "n_context": 0,
            "n_hits": 0,
            "contexts": [],
            "direct_answer": guarded,
            "scope": scope.scope_key,
            "team_scope": scope.team_filter,
            "latency_ms": int((time.time() - t0) * 1000),
            "search_q": q,
        }

    refs = dict(context_refs or {})
    # 兼容旧路径：未显式传 search_q 时走 router
    sq = (search_q or "").strip() or retrieval_query(q, history or [])
    # seed chunk / item → 问句 hint（定位素材，不当证据）
    seed_ids = [str(x) for x in (refs.get("chunk_ids") or []) if x][:24]
    seed_items = [str(x) for x in (refs.get("item_ids") or []) if x][:24]
    if seed_ids:
        hints = retriever._chunk_hint_terms(con, seed_ids)
        for h in hints:
            if h and h not in sq:
                sq = f"{sq} {h}".strip()[:500]

    # Planner-lite：原始问句规划 structured/hybrid（不扩写 search_q）
    plan = plan_retrieval(q, context_refs)
    planner_fallback = plan.set_op != "none" and plan.confidence < CONFIDENCE_THRESHOLD
    intent = plan.intent if plan.path == "structured" and plan.intent else None
    structured_candidate = bool(intent)

    def _plan_meta() -> dict:
        return {
            "set_op": plan.set_op,
            "path": plan.path,
            "confidence": plan.confidence,
        }
    query_vec = None
    if not structured_candidate and embeddings.is_configured():
        query_vec = embeddings.embed_one(sq)
    if intent and intent.get("type") == "error":
        return {
            "mode": "structured", "n_context": 0, "contexts": [],
            "direct_answer": intent.get("message") or "无法解析该交叉问题",
            "scope": scope.scope_key, "latency_ms": 0, "n_hits": 0,
            "retrieval_plan": _plan_meta(),
        }

    mode, hits, meta = retriever.retrieve(
        con, sq, scope, intent=intent, query_vec=query_vec,
        seed_chunk_ids=seed_ids or None, seed_item_ids=seed_items or None,
    )
    latency = int((time.time() - t0) * 1000)

    if mode == "structured":
        st = meta.get("structured") or {}
        if not st.get("ok"):
            return {
                "mode": "structured", "n_context": 0, "contexts": [],
                "direct_answer": st.get("message") or "结构化检索失败",
                "scope": scope.scope_key, "latency_ms": latency, "n_hits": 0,
                "retrieval_plan": _plan_meta(),
            }
        ctxs = st["contexts"]
        intent_out = st.get("intent") or {}
        total = int(st.get("total") or 0)
        if total == 0:
            return {
                "mode": "structured",
                "n_context": 0,
                "n_hits": 0,
                "total": 0,
                "query": intent_out,
                "date_from": intent_out.get("date_from"),
                "date_to": intent_out.get("date_to"),
                "contexts": ctxs,
                "direct_answer": qa_structured.empty_result_answer(intent_out),
                "scope": scope.scope_key,
                "team_scope": scope.team_filter,
                "latency_ms": latency,
                "search_q": sq,
                "retrieval_plan": _plan_meta(),
            }
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
            "search_q": sq,
            "retrieval_plan": _plan_meta(),
        }

    ctxs = retriever.hits_to_contexts(hits, sq)
    if planner_fallback and plan.fallback_note:
        ctxs.insert(0, {
            "期号": "",
            "章节": "检索说明",
            "标题": "检索路径",
            "内容": plan.fallback_note,
        })
    n_ctx = len(ctxs)
    if meta.get("date_from") or meta.get("date_to"):
        n_ctx = max(0, n_ctx - 1)
    report_mode = "hybrid" if meta.get("used_vector") else "lexical"
    if n_ctx == 0:
        da = _lexical_no_evidence_answer(meta)
        if planner_fallback and plan.fallback_note:
            da = plan.fallback_note + "\n\n" + da
        return {
            "mode": report_mode,
            "n_context": 0,
            "n_hits": 0,
            "date_from": meta.get("date_from"),
            "date_to": meta.get("date_to"),
            "contexts": [],
            "direct_answer": da,
            "scope": scope.scope_key,
            "team_scope": scope.team_filter,
            "latency_ms": latency,
            "search_q": sq,
            "retrieval_plan": _plan_meta(),
            "planner_fallback": planner_fallback,
        }
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
        "search_q": sq,
        "retrieval_plan": _plan_meta(),
        "planner_fallback": planner_fallback,
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
