"""多轮检索问句：委托 Context Router（仅用 context_refs，不用 Answer）。"""
from __future__ import annotations

from . import ask_context


def retrieval_query(q: str, history: list[dict] | None = None, *, messages: list[dict] | None = None) -> str:
    msgs = messages if messages is not None else history
    return ask_context.route(q, msgs).get("search_q") or (q or "").strip()
