"""feishu.search — 统一 resource_type。"""
from __future__ import annotations

from typing import Any

from ..tool_contract import (
    FEISHU_SEARCH,
    ToolResultEnvelope,
    feishu_search_type_allowed,
)
from . import backends, flags
from .normalize import envelope_fail, envelope_ok


def search(
    query: str,
    *,
    resource_type: str = "doc",
    identity: Any = None,
    user_access_token: str = "",
    chat_id: str = "",
    phase: str = "full",
    keyword: str = "",
    open_ids: list[str] | None = None,
    user_open_id: str = "",
) -> ToolResultEnvelope:
    if not flags.hands_enabled():
        return envelope_fail("hands_disabled")

    q = (query or "").strip()
    rt = (resource_type or "doc").strip().lower() or "doc"
    # 群列表 / 日历 / 群成员 / 按 id 查人 / 通讯录：允许空 query
    if not q and rt not in ("group", "calendar", "member", "user", "directory"):
        return envelope_ok([])

    if not feishu_search_type_allowed(rt, phase=phase):
        return envelope_fail(f"resource_type_not_allowed:{rt}")

    open_id = ""
    if identity is not None:
        open_id = str(getattr(identity, "feishu_open_id", None) or "").strip()

    return backends.call_tool(
        "feishu.search",
        {
            "query": q,
            "resource_type": rt,
            "max_results": FEISHU_SEARCH.max_results,
            "chat_id": (chat_id or "").strip(),
            "keyword": keyword,
            "open_ids": list(open_ids or []),
            "open_id": str(user_open_id or "").strip(),
        },
        timeout_sec=FEISHU_SEARCH.timeout_sec,
        user_access_token=(user_access_token or "").strip(),
        open_id=open_id,
    )
