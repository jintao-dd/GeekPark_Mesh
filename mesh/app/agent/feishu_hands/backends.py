"""Hands 统一后端：MCP 为主；mock 可注入；openapi/cli 仅搜索过渡。"""
from __future__ import annotations

import json
import logging
import subprocess
from typing import Any, Callable

import requests

from . import flags
from .normalize import envelope_fail, envelope_ok, normalize_docs
from ..tool_contract import FEISHU_SEARCH, ToolResultEnvelope

log = logging.getLogger("mesh.feishu_hands")

OpsFn = Callable[[str, dict[str, Any]], ToolResultEnvelope]
_injected_ops: OpsFn | None = None


def set_ops_backend(fn: OpsFn | None) -> None:
    global _injected_ops
    _injected_ops = fn


def clear_ops_backend() -> None:
    set_ops_backend(None)


# 兼容旧 search 注入
_injected_search: Callable[..., ToolResultEnvelope] | None = None


def set_search_backend(fn: Callable[..., ToolResultEnvelope] | None) -> None:
    global _injected_search
    _injected_search = fn


def clear_search_backend() -> None:
    set_search_backend(None)


def _mcp_post(
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout_sec: float,
    user_access_token: str = "",
    open_id: str = "",
) -> ToolResultEnvelope:
    url = flags.mcp_endpoint()
    if not url:
        return envelope_fail("mcp_not_configured", tool=tool)
    headers = {"Content-Type": "application/json"}
    if user_access_token:
        headers["X-Feishu-User-Token"] = user_access_token
    payload = {
        "tool": tool,
        "arguments": dict(arguments or {}),
        "open_id": open_id or "",
    }
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=timeout_sec)
        if r.status_code >= 400:
            return envelope_fail(f"mcp_http_{r.status_code}", tool=tool)
        data = r.json() if r.content else {}
        if not isinstance(data, dict):
            return envelope_fail("mcp_bad_payload", tool=tool)
        if data.get("ok") is False:
            return envelope_fail(str(data.get("error") or "mcp_error"), tool=tool)
        items = data.get("items") or data.get("docs_entities") or []
        if not isinstance(items, list):
            items = []
        kind = str(arguments.get("resource_type") or "doc")
        meta = {k: data[k] for k in ("url", "event_id", "message_id", "doc_token") if k in data}
        return envelope_ok(normalize_docs(items, kind=kind), tool=tool, meta=meta)
    except requests.Timeout:
        return envelope_fail("mcp_timeout", tool=tool)
    except Exception as e:
        log.warning("mcp call %s failed: %s", tool, e)
        return envelope_fail(f"mcp_error:{type(e).__name__}", tool=tool)


def _openapi_search_docs(
    query: str,
    *,
    max_results: int,
    timeout_sec: float,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    token = (user_access_token or "").strip()
    if not token:
        return envelope_fail("user_token_required", tool="feishu.search")
    try:
        r = requests.post(
            "https://open.feishu.cn/open-apis/suite/docs-api/search/object",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "search_key": query,
                "count": max(1, min(int(max_results), 50)),
                "offset": 0,
                "docs_types": ["doc", "docx", "sheet", "bitable", "file"],
            },
            timeout=timeout_sec,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400 or int(data.get("code") or 0) != 0:
            return envelope_fail(
                f"openapi_{data.get('code') or r.status_code}", tool="feishu.search"
            )
        entities = (data.get("data") or {}).get("docs_entities") or []
        return envelope_ok(
            normalize_docs(entities if isinstance(entities, list) else [], kind="doc"),
            tool="feishu.search",
        )
    except requests.Timeout:
        return envelope_fail("openapi_timeout", tool="feishu.search")
    except Exception as e:
        return envelope_fail(f"openapi_error:{type(e).__name__}", tool="feishu.search")


def _cli_search(query: str, *, max_results: int, timeout_sec: float) -> ToolResultEnvelope:
    bin_path = flags.cli_bin()
    if not bin_path:
        return envelope_fail("cli_not_configured", tool="feishu.search")
    try:
        proc = subprocess.run(
            [bin_path, "docs", "search", "--query", query, "--limit", str(max_results), "--json"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
        if proc.returncode != 0:
            return envelope_fail(f"cli_exit_{proc.returncode}", tool="feishu.search")
        data = json.loads(proc.stdout or "{}")
        items = data if isinstance(data, list) else (data.get("items") or [])
        return envelope_ok(
            normalize_docs(items if isinstance(items, list) else [], kind="doc"),
            tool="feishu.search",
        )
    except subprocess.TimeoutExpired:
        return envelope_fail("cli_timeout", tool="feishu.search")
    except Exception as e:
        return envelope_fail(f"cli_error:{type(e).__name__}", tool="feishu.search")


def _mock_call(tool: str, arguments: dict[str, Any]) -> ToolResultEnvelope:
    """本地/联调 mock：不碰真实飞书，便于测确认流与 Brain。"""
    args = dict(arguments or {})
    if tool == "feishu.search":
        rt = str(args.get("resource_type") or "doc")
        q = str(args.get("query") or "")
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": f"[mock:{rt}] {q or '结果'}",
                        "snippet": "mock live_context",
                        "docs_token": "mock_tok",
                        "docs_type": rt if rt != "calendar" else "event",
                    }
                ],
                kind=rt,
            ),
            tool=tool,
        )
    if tool == "feishu.doc.get":
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": args.get("query") or "文档要点",
                        "snippet": "（mock）文档摘要内容。",
                        "url": args.get("url") or "https://feishu.cn/docx/mock",
                    }
                ]
            ),
            tool=tool,
        )
    if tool == "feishu.calendar.list":
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": "周会",
                        "snippet": "明天 10:00-11:00",
                        "docs_type": "event",
                        "token": "evt_mock",
                    }
                ],
                kind="calendar",
            ),
            tool=tool,
        )
    if tool == "feishu.discuss.summary":
        person = str(args.get("person") or args.get("query") or "")
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": f"{person} 最近讨论",
                        "snippet": "（mock）提到项目进度与下周计划",
                        "docs_type": "message",
                    }
                ],
                kind="message",
            ),
            tool=tool,
        )
    if tool == "feishu.doc.create":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        title = str(args.get("title") or "未命名")
        return envelope_ok(
            [{"title": title, "url": "https://feishu.cn/docx/mock_created", "snippet": "已创建"}],
            tool=tool,
            meta={"url": "https://feishu.cn/docx/mock_created"},
        )
    if tool == "feishu.im.send":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        return envelope_ok(
            [
                {
                    "title": "已发送",
                    "snippet": str(args.get("text") or "")[:80],
                    "docs_type": "message",
                }
            ],
            tool=tool,
            meta={"message_id": "om_mock"},
        )
    if tool == "feishu.calendar.create":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        return envelope_ok(
            [
                {
                    "title": str(args.get("title") or "日程"),
                    "snippet": f"{args.get('start')} ~ {args.get('end')}",
                    "docs_type": "event",
                }
            ],
            tool=tool,
            meta={"event_id": "evt_created"},
        )
    return envelope_fail(f"unknown_tool:{tool}", tool=tool)


def call_tool(
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout_sec: float | None = None,
    user_access_token: str = "",
    open_id: str = "",
) -> ToolResultEnvelope:
    args = dict(arguments or {})
    to = float(timeout_sec if timeout_sec is not None else FEISHU_SEARCH.timeout_sec)

    if _injected_ops is not None:
        return _injected_ops(tool, args)

    backend = flags.backend_name()
    if backend == "mock":
        return _mock_call(tool, args)

    if tool == "feishu.search" and _injected_search is not None:
        return _injected_search(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or FEISHU_SEARCH.max_results),
            timeout_sec=to,
            user_access_token=user_access_token,
            open_id=open_id,
        )

    if backend == "openapi" and tool == "feishu.search":
        if str(args.get("resource_type") or "doc") != "doc":
            return envelope_fail("openapi_doc_only", tool=tool)
        return _openapi_search_docs(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or FEISHU_SEARCH.max_results),
            timeout_sec=to,
            user_access_token=user_access_token,
        )

    if backend == "cli" and tool == "feishu.search":
        return _cli_search(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or FEISHU_SEARCH.max_results),
            timeout_sec=to,
        )

    return _mcp_post(
        tool,
        args,
        timeout_sec=to,
        user_access_token=user_access_token,
        open_id=open_id,
    )


def run_search(
    query: str,
    *,
    resource_type: str = "doc",
    max_results: int | None = None,
    timeout_sec: float | None = None,
    user_access_token: str = "",
    open_id: str = "",
) -> ToolResultEnvelope:
    return call_tool(
        "feishu.search",
        {
            "query": query,
            "resource_type": resource_type,
            "max_results": int(max_results if max_results is not None else FEISHU_SEARCH.max_results),
        },
        timeout_sec=timeout_sec,
        user_access_token=user_access_token,
        open_id=open_id,
    )
