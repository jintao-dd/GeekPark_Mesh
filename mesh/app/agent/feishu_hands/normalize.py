"""把官方/MCP/CLI 原始结果压成 Contract output_schema。"""
from __future__ import annotations

from typing import Any

from ..tool_contract import (
    FEISHU_SEARCH,
    SourceTier,
    ToolResultEnvelope,
    TruthLevel,
)


def envelope_ok(items: list[dict[str, Any]], *, tool: str = "feishu.search") -> ToolResultEnvelope:
    capped = list(items or [])[: int(FEISHU_SEARCH.max_results)]
    return ToolResultEnvelope(
        ok=True,
        tool=tool,
        source_tier=SourceTier.FEISHU_LIVE,
        truth_level=TruthLevel.LIVE_CONTEXT,
        items=capped,
        empty=not bool(capped),
    )


def envelope_fail(error: str, *, tool: str = "feishu.search") -> ToolResultEnvelope:
    return ToolResultEnvelope(
        ok=False,
        tool=tool,
        source_tier=SourceTier.FEISHU_LIVE,
        truth_level=TruthLevel.LIVE_CONTEXT,
        items=[],
        error=(error or "feishu_hands_error")[:200],
        empty=True,
    )


def normalize_doc_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    title = str(
        raw.get("title")
        or raw.get("name")
        or raw.get("docs_title")
        or ""
    ).strip()
    url = str(raw.get("url") or raw.get("link") or raw.get("docs_url") or "").strip()
    token = str(raw.get("docs_token") or raw.get("token") or raw.get("id") or "").strip()
    docs_type = str(raw.get("docs_type") or raw.get("type") or "doc").strip() or "doc"
    if not url and token:
        # 公开可点开形态；无权限时飞书侧仍会拦
        if docs_type in ("docx", "doc"):
            url = f"https://feishu.cn/docx/{token}"
        elif docs_type == "wiki":
            url = f"https://feishu.cn/wiki/{token}"
        else:
            url = f"https://feishu.cn/drive/folder/{token}"
    snippet = str(
        raw.get("snippet")
        or raw.get("summary")
        or raw.get("summary_highlighted")
        or raw.get("title_highlighted")
        or ""
    ).strip()
    if not title and not url:
        return None
    permission_ok = raw.get("permission_ok")
    if permission_ok is None:
        permission_ok = True
    return {
        "title": title or "(无标题)",
        "snippet": snippet[:400],
        "url": url,
        "permission_ok": bool(permission_ok),
        "docs_type": docs_type,
        "source_tier": SourceTier.FEISHU_LIVE.value,
        "truth_level": TruthLevel.LIVE_CONTEXT.value,
    }


def normalize_docs(raw_items: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for it in raw_items or []:
        n = normalize_doc_item(it if isinstance(it, dict) else {})
        if n:
            out.append(n)
    return out
