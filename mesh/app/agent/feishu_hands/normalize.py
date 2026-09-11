"""规范化 Hands 结果。"""
from __future__ import annotations

from typing import Any

from ..tool_contract import (
    FEISHU_SEARCH,
    SourceTier,
    ToolResultEnvelope,
    TruthLevel,
)


def envelope_ok(
    items: list[dict[str, Any]],
    *,
    tool: str = "feishu.search",
    meta: dict[str, Any] | None = None,
) -> ToolResultEnvelope:
    capped = list(items or [])[: int(FEISHU_SEARCH.max_results)]
    return ToolResultEnvelope(
        ok=True,
        tool=tool,
        source_tier=SourceTier.FEISHU_LIVE,
        truth_level=TruthLevel.LIVE_CONTEXT,
        items=capped,
        empty=not bool(capped),
        meta=dict(meta or {}),
    )


def envelope_fail(
    error: str,
    *,
    tool: str = "feishu.search",
    meta: dict[str, Any] | None = None,
) -> ToolResultEnvelope:
    return ToolResultEnvelope(
        ok=False,
        tool=tool,
        source_tier=SourceTier.FEISHU_LIVE,
        truth_level=TruthLevel.LIVE_CONTEXT,
        items=[],
        error=(error or "feishu_hands_error")[:200],
        empty=True,
        meta=dict(meta or {}),
    )


def normalize_item(raw: dict[str, Any], *, kind: str = "doc") -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    title = str(
        raw.get("title")
        or raw.get("name")
        or raw.get("summary")
        or raw.get("text")
        or ""
    ).strip()
    url = str(raw.get("url") or raw.get("link") or "").strip()
    token = str(
        raw.get("docs_token") or raw.get("token") or raw.get("event_id") or raw.get("id") or ""
    ).strip()
    docs_type = str(raw.get("docs_type") or raw.get("type") or kind).strip() or kind
    if not url and token:
        if docs_type in ("docx", "doc"):
            url = f"https://feishu.cn/docx/{token}"
        elif docs_type in ("wiki",):
            url = f"https://feishu.cn/wiki/{token}"
        elif docs_type in ("message",):
            url = ""
        elif docs_type in ("calendar", "event"):
            url = f"https://feishu.cn/calendar/{token}"
        elif docs_type in ("member", "user", "person", "group", "message"):
            url = ""
        else:
            url = f"https://feishu.cn/drive/folder/{token}"
    snippet = str(
        raw.get("snippet")
        or raw.get("summary")
        or raw.get("summary_highlighted")
        or raw.get("content")
        or raw.get("start")
        or ""
    ).strip()
    if not title and not url and not snippet:
        return None
    permission_ok = raw.get("permission_ok")
    if permission_ok is None:
        permission_ok = True
    return {
        "title": title or "(无标题)",
        "snippet": snippet[:500],
        "url": url,
        "permission_ok": bool(permission_ok),
        "docs_type": docs_type,
        "source_tier": SourceTier.FEISHU_LIVE.value,
        "truth_level": TruthLevel.LIVE_CONTEXT.value,
    }


def normalize_docs(raw_items: list[Any], *, kind: str = "doc") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in raw_items or []:
        n = normalize_item(it if isinstance(it, dict) else {}, kind=kind)
        if n:
            out.append(n)
    return out
