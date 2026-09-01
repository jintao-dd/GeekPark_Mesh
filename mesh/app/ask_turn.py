"""单次问答回合：缓存、检索、落库（流式/非流式共用）。"""
from __future__ import annotations

import time
from typing import Any

from . import ask_citation, ask_context, ask_engine, ask_store, conversation, llm, db
from .ask_scope import AskScope


def _resp_from_prepared(prepared: dict, ans: str, session_id: str) -> dict:
    resp = {k: v for k, v in prepared.items() if k not in ("contexts", "direct_answer", "preview")}
    resp["answer"] = ans
    resp["session_id"] = session_id
    return resp


def begin_turn(
    con,
    *,
    user: dict,
    scope: AskScope,
    q: str,
    cache_get,
    cache_put,
) -> dict[str, Any]:
    """
    准备一轮问答。返回:
    - cached: 若命中缓存则直接给 resp
    - 否则 sess, context_route, history=[]（禁止 Answer 进成文）, cache_key, use_cache
    """
    sess = conversation.ensure_session(con, scope, user)
    raw_messages = conversation.recent_messages(con, sess["id"], limit=6)
    ctx_route = ask_context.route(q, raw_messages)
    # 成文/分析禁止继承对话 history（含历史 Answer）
    history: list[dict] = []
    use_cache = ctx_route.get("kind") == "independent"
    parent = ctx_route.get("parent_analysis_id") or ""
    cache_key = scope.cache_key(
        q,
        session_id=sess["id"],
        history_len=0 if use_cache else (1 if parent else 1),
    )

    if use_cache:
        cached = cache_get(cache_key)
        if cached and cached[0] > time.time():
            resp = dict(cached[1])
            ans = resp.get("answer") or ""
            conversation.append_turn(
                con, sess["id"], user_text=q, assistant_text=ans,
                mode=resp.get("mode", ""), n_context=resp.get("n_context", 0),
                meta={
                    "cached": True,
                    "context_route": ctx_route.get("kind"),
                    "analysis_id": resp.get("analysis_id") or resp.get("report_id"),
                    "context_refs": resp.get("context_refs"),
                },
            )
            ask_engine.log_ask(
                con, scope, user, q,
                {**resp, "latency_ms": 0, "n_hits": resp.get("n_context", 0)},
                session_id=sess["id"],
            )
            with db.write_lock():
                db.commit_retry(con)
            resp["session_id"] = sess["id"]
            resp["cached"] = True
            return {"cached": True, "resp": resp}

    with db.write_lock():
        db.commit_retry(con)

    return {
        "cached": False,
        "sess": sess,
        "history": history,
        "raw_history": raw_messages,
        "context_route": ctx_route,
        "cache_key": cache_key,
        "use_cache": use_cache,
    }


def prepare_turn(con, q: str, scope: AskScope, history: list[dict] | None = None, *, context_route: dict | None = None) -> dict:
    """检索组装上下文。追问只用 context_refs / search_q，不用对话 history。"""
    route = context_route or ask_context.route(q, history or [])
    return ask_engine.prepare(
        con,
        q,
        scope,
        history=None,
        search_q=route.get("search_q") or q,
        context_refs=route.get("context_refs") if route.get("kind") == "followup" else None,
    )


def complete_turn(
    con,
    *,
    user: dict,
    scope: AskScope,
    q: str,
    sess: dict,
    prepared: dict,
    ans: str,
    cache_key: str,
    use_cache: bool,
    cache_put,
) -> dict:
    analysis_id = prepared.get("analysis_id") or prepared.get("report_id") or ""
    refs = prepared.get("context_refs")
    if not isinstance(refs, dict) or not refs.get("analysis_id"):
        refs = ask_context.build_context_refs(
            analysis_id=analysis_id or ("turn-" + str(int(time.time()))),
            q=q,
            contexts=prepared.get("contexts") or [],
            sources=prepared.get("sources") if isinstance(prepared.get("sources"), list) else None,
            cross=prepared.get("cross") if isinstance(prepared.get("cross"), dict) else None,
            parent_analysis_id=(prepared.get("context_route") or {}).get("parent_analysis_id")
            if isinstance(prepared.get("context_route"), dict)
            else None,
        )
        prepared["context_refs"] = refs
        if not prepared.get("analysis_id"):
            prepared["analysis_id"] = refs.get("analysis_id")
            prepared["report_id"] = prepared["analysis_id"]

    conversation.append_turn(
        con, sess["id"], user_text=q, assistant_text=ans,
        mode=prepared.get("mode", ""), n_context=prepared.get("n_context", 0),
        meta={
            "analysis_id": prepared.get("analysis_id") or refs.get("analysis_id"),
            "context_refs": refs,
            "context_route": (prepared.get("context_route") or {}).get("kind")
            if isinstance(prepared.get("context_route"), dict)
            else None,
        },
    )
    try:
        aid = prepared.get("analysis_id") or refs.get("analysis_id")
        if aid:
            ask_store.finish(
                con,
                analysis_id=str(aid),
                answer=ans,
                context_refs=refs,
                usage=prepared.get("usage") if isinstance(prepared.get("usage"), dict) else None,
                verify=prepared.get("verify") if isinstance(prepared.get("verify"), dict) else None,
                sources=prepared.get("sources") if isinstance(prepared.get("sources"), list) else None,
                status="completed",
            )
    except Exception:
        pass
    ask_engine.log_ask(con, scope, user, q, prepared, session_id=sess["id"])
    with db.write_lock():
        db.commit_retry(con)
    resp = _resp_from_prepared(prepared, ans, sess["id"])
    if use_cache:
        cache_put(cache_key, resp)
    return resp


def _ground_answer(ans: str, prepared: dict) -> str:
    if prepared.get("direct_answer") is not None:
        return ans
    result = ask_citation.validate_answer(ans, prepared.get("contexts") or [])
    if result.get("flags"):
        prepared["citation_flags"] = result["flags"]
    return result.get("answer") or ans


def generate_answer(q: str, prepared: dict, history: list[dict] | None = None) -> str:
    """history 忽略：禁止历史 Answer 进入成文。"""
    _ = history
    if prepared.get("direct_answer") is not None:
        return prepared["direct_answer"]
    ans = llm.answer_question(q, prepared["contexts"], mode=prepared["mode"], history=None)
    return _ground_answer(ans, prepared)


def generate_answer_stream(q: str, prepared: dict, history: list[dict] | None = None):
    _ = history
    if prepared.get("direct_answer") is not None:
        ans = prepared["direct_answer"]
        if ans:
            yield ans
        return
    parts: list[str] = []
    for chunk in llm.answer_question_stream(q, prepared["contexts"], mode=prepared["mode"], history=None):
        if chunk:
            parts.append(chunk)
    ans = _ground_answer("".join(parts), prepared)
    step = 48
    for i in range(0, len(ans), step):
        yield ans[i : i + step]
