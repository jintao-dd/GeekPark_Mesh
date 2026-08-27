"""单次问答回合：缓存、检索、落库（流式/非流式共用）。"""
from __future__ import annotations

import time
from typing import Any

from . import ask_engine, conversation, llm, db
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
    - 否则 sess, history, prepared, cache_key, use_cache, retrieval_q
    """
    sess = conversation.ensure_session(con, scope, user)
    history = conversation.recent_messages(con, sess["id"], limit=6)
    use_cache = len(history) == 0
    cache_key = scope.cache_key(q, session_id=sess["id"], history_len=len(history))

    if use_cache:
        cached = cache_get(cache_key)
        if cached and cached[0] > time.time():
            resp = dict(cached[1])
            ans = resp.get("answer") or ""
            conversation.append_turn(
                con, sess["id"], user_text=q, assistant_text=ans,
                mode=resp.get("mode", ""), n_context=resp.get("n_context", 0),
                meta={"cached": True},
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

    prepared = ask_engine.prepare(con, q, scope, history=history)
    return {
        "cached": False,
        "sess": sess,
        "history": history,
        "prepared": prepared,
        "cache_key": cache_key,
        "use_cache": use_cache,
    }


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
    conversation.append_turn(
        con, sess["id"], user_text=q, assistant_text=ans,
        mode=prepared.get("mode", ""), n_context=prepared.get("n_context", 0),
    )
    ask_engine.log_ask(con, scope, user, q, prepared, session_id=sess["id"])
    with db.write_lock():
        db.commit_retry(con)
    resp = _resp_from_prepared(prepared, ans, sess["id"])
    if use_cache:
        cache_put(cache_key, resp)
    return resp


def generate_answer(q: str, prepared: dict, history: list[dict]) -> str:
    if prepared.get("direct_answer") is not None:
        return prepared["direct_answer"]
    return llm.answer_question(q, prepared["contexts"], mode=prepared["mode"], history=history)


def generate_answer_stream(q: str, prepared: dict, history: list[dict]):
    if prepared.get("direct_answer") is not None:
        ans = prepared["direct_answer"]
        if ans:
            yield ans
        return
    yield from llm.answer_question_stream(q, prepared["contexts"], mode=prepared["mode"], history=history)
