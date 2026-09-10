"""Hands 读/写操作（除 search 外）。写必须 confirmed + WRITE 开关。"""
from __future__ import annotations

from typing import Any

from ..tool_contract import (
    FEISHU_CALENDAR_CREATE,
    FEISHU_CALENDAR_LIST,
    FEISHU_DISCUSS_SUMMARY,
    FEISHU_DOC_CREATE,
    FEISHU_DOC_GET,
    FEISHU_IM_SEND,
    ToolResultEnvelope,
)
from . import backends, flags
from .normalize import envelope_fail


def _open_id(identity: Any) -> str:
    if identity is None:
        return ""
    return str(getattr(identity, "feishu_open_id", None) or "").strip()


def _guard_read() -> ToolResultEnvelope | None:
    if not flags.hands_enabled():
        return envelope_fail("hands_disabled")
    return None


def _guard_write(*, confirmed: bool, tool: str) -> ToolResultEnvelope | None:
    if not flags.hands_enabled():
        return envelope_fail("hands_disabled", tool=tool)
    if not flags.write_enabled():
        return envelope_fail("write_disabled", tool=tool)
    if not confirmed:
        return envelope_fail("confirmation_required", tool=tool)
    return None


def doc_get(
    *,
    doc_token: str = "",
    url: str = "",
    query: str = "",
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_read()
    if blocked:
        return blocked
    return backends.call_tool(
        FEISHU_DOC_GET.name,
        {"doc_token": doc_token, "url": url, "query": query},
        timeout_sec=FEISHU_DOC_GET.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )


def calendar_list(
    *,
    query: str = "",
    days: int = 7,
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_read()
    if blocked:
        return blocked
    return backends.call_tool(
        FEISHU_CALENDAR_LIST.name,
        {"query": query, "days": int(days or 7)},
        timeout_sec=FEISHU_CALENDAR_LIST.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )


def discuss_summary(
    *,
    query: str,
    person: str = "",
    chat_id: str = "",
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_read()
    if blocked:
        return blocked
    # 组合能力：优先专用 tool；失败可退化 message search（由 MCP 侧实现）
    return backends.call_tool(
        FEISHU_DISCUSS_SUMMARY.name,
        {"query": query, "person": person, "chat_id": chat_id},
        timeout_sec=FEISHU_DISCUSS_SUMMARY.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )


def doc_create(
    *,
    title: str,
    content: str,
    confirmed: bool = False,
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_write(confirmed=confirmed, tool=FEISHU_DOC_CREATE.name)
    if blocked:
        return blocked
    return backends.call_tool(
        FEISHU_DOC_CREATE.name,
        {"title": title, "content": content, "confirmed": True},
        timeout_sec=FEISHU_DOC_CREATE.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )


def im_send(
    *,
    receive_id: str,
    text: str,
    receive_id_type: str = "chat_id",
    confirmed: bool = False,
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_write(confirmed=confirmed, tool=FEISHU_IM_SEND.name)
    if blocked:
        return blocked
    return backends.call_tool(
        FEISHU_IM_SEND.name,
        {
            "receive_id": receive_id,
            "receive_id_type": receive_id_type or "chat_id",
            "text": text,
            "confirmed": True,
        },
        timeout_sec=FEISHU_IM_SEND.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )


def calendar_create(
    *,
    title: str,
    start: str,
    end: str,
    description: str = "",
    confirmed: bool = False,
    identity: Any = None,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    blocked = _guard_write(confirmed=confirmed, tool=FEISHU_CALENDAR_CREATE.name)
    if blocked:
        return blocked
    return backends.call_tool(
        FEISHU_CALENDAR_CREATE.name,
        {
            "title": title,
            "start": start,
            "end": end,
            "description": description,
            "confirmed": True,
        },
        timeout_sec=FEISHU_CALENDAR_CREATE.timeout_sec,
        user_access_token=user_access_token,
        open_id=_open_id(identity),
    )
