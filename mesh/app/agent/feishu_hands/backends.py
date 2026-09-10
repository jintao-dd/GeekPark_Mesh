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


def _cli_bin() -> str:
    return flags.cli_bin() or "lark-cli"


def _cli_run(argv: list[str], *, timeout_sec: float, tool: str) -> ToolResultEnvelope:
    import os

    bin_path = _cli_bin()
    try:
        proc = subprocess.run(
            [bin_path, *argv, "--format", "json"],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        return envelope_fail("cli_not_installed", tool=tool)
    except subprocess.TimeoutExpired:
        return envelope_fail("cli_timeout", tool=tool)
    except Exception as e:
        return envelope_fail(f"cli_error:{type(e).__name__}", tool=tool)
    raw_out = (proc.stdout or "").strip()
    raw_err = (proc.stderr or "").strip()
    data: dict[str, Any] = {}
    try:
        data = json.loads(raw_out) if raw_out else {}
    except Exception:
        data = {}
    if proc.returncode != 0 or (isinstance(data, dict) and data.get("ok") is False):
        err = ""
        if isinstance(data, dict):
            eobj = data.get("error") or {}
            if isinstance(eobj, dict):
                err = str(eobj.get("message") or eobj.get("hint") or "")
            err = err or str(data.get("error") or "")
        err = err or raw_err[:160] or f"cli_exit_{proc.returncode}"
        return envelope_fail(f"cli:{err[:160]}", tool=tool)
    if not isinstance(data, dict):
        data = {"data": data}
    return envelope_ok([], tool=tool, meta={"cli": data})


def _cli_call(tool: str, arguments: dict[str, Any], *, timeout_sec: float) -> ToolResultEnvelope:
    """官方 lark-cli shortcuts（与 larksuite/cli Skills 同源命令面）。"""
    args = dict(arguments or {})
    if tool == "feishu.search":
        rt = str(args.get("resource_type") or "doc").lower()
        q = str(args.get("query") or "").strip()
        mr = str(int(args.get("max_results") or 8))
        if rt in ("doc", "folder", "wiki"):
            env = _cli_run(["docs", "+search", "--query", q, "--limit", mr], timeout_sec=timeout_sec, tool=tool)
            if not env.ok:
                return env
            payload = (env.meta or {}).get("cli") or {}
            items = payload.get("data") if isinstance(payload, dict) else []
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                items = payload.get("items")
            if not isinstance(items, list):
                items = []
            return envelope_ok(normalize_docs(items, kind=rt), tool=tool)
        if rt == "message":
            chat_id = str(args.get("chat_id") or "").strip()
            argv = ["im", "+messages-search", "--query", q]
            if chat_id:
                argv += ["--chat-id", chat_id]
            env = _cli_run(argv, timeout_sec=timeout_sec, tool=tool)
            if not env.ok:
                return env
            payload = (env.meta or {}).get("cli") or {}
            items = payload.get("data") if isinstance(payload, dict) else []
            if isinstance(payload, dict) and isinstance(payload.get("items"), list):
                items = payload.get("items")
            return envelope_ok(normalize_docs(items if isinstance(items, list) else [], kind="message"), tool=tool)
        return envelope_fail(f"cli_resource_unsupported:{rt}", tool=tool)

    if tool == "feishu.doc.get":
        token = str(args.get("doc_token") or "").strip()
        url = str(args.get("url") or "").strip()
        argv = ["docs", "+fetch"]
        if token:
            argv += ["--doc", token]
        elif url:
            argv += ["--doc", url]
        else:
            return envelope_fail("doc_token_or_url_required", tool=tool)
        env = _cli_run(argv, timeout_sec=timeout_sec, tool=tool)
        if not env.ok:
            return env
        payload = (env.meta or {}).get("cli") or {}
        data = payload.get("data") if isinstance(payload, dict) else {}
        title = str((data or {}).get("title") or token or "文档")
        snippet = str((data or {}).get("content") or (data or {}).get("markdown") or "")[:500]
        return envelope_ok(
            normalize_docs([{"title": title, "snippet": snippet, "url": url or "", "docs_token": token}]),
            tool=tool,
        )

    if tool == "feishu.calendar.list":
        return _cli_run(["calendar", "+agenda"], timeout_sec=timeout_sec, tool=tool)

    if tool == "feishu.discuss.summary":
        chat_id = str(args.get("chat_id") or "").strip()
        q = str(args.get("query") or args.get("person") or "").strip() or " "
        return _cli_call(
            "feishu.search",
            {"resource_type": "message", "query": q, "chat_id": chat_id, "max_results": 10},
            timeout_sec=timeout_sec,
        )

    if tool == "feishu.doc.create":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        title = str(args.get("title") or "未命名文档")
        content = str(args.get("content") or "")
        md = f"<title>{title}</title>\n{content}"
        env = _cli_run(
            ["docs", "+create", "--doc-format", "markdown", "--content", md],
            timeout_sec=timeout_sec,
            tool=tool,
        )
        if not env.ok:
            return env
        payload = (env.meta or {}).get("cli") or {}
        data = payload.get("data") if isinstance(payload, dict) else {}
        url = str((data or {}).get("url") or (data or {}).get("doc_url") or "")
        token = str((data or {}).get("document_id") or (data or {}).get("doc_token") or "")
        if not url and token:
            url = f"https://feishu.cn/docx/{token}"
        return envelope_ok(
            [{"title": title, "url": url, "snippet": "已创建", "docs_token": token}],
            tool=tool,
            meta={"url": url, "doc_token": token},
        )

    if tool == "feishu.im.send":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        rid = str(args.get("receive_id") or args.get("chat_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not rid or not text:
            return envelope_fail("receive_id_and_text_required", tool=tool)
        env = _cli_run(
            ["im", "+messages-send", "--chat-id", rid, "--text", text],
            timeout_sec=timeout_sec,
            tool=tool,
        )
        if not env.ok:
            return env
        return envelope_ok(
            [{"title": "已发送", "snippet": text[:120], "docs_type": "message"}],
            tool=tool,
        )

    if tool == "feishu.calendar.create":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        title = str(args.get("title") or "日程")
        start = str(args.get("start") or "")
        end = str(args.get("end") or "")
        argv = ["calendar", "+create", "--summary", title]
        if start:
            argv += ["--start", start]
        if end:
            argv += ["--end", end]
        env = _cli_run(argv, timeout_sec=timeout_sec, tool=tool)
        if not env.ok:
            return env
        return envelope_ok(
            [{"title": title, "snippet": f"{start} ~ {end}", "docs_type": "event"}],
            tool=tool,
        )

    return envelope_fail(f"cli_unknown_tool:{tool}", tool=tool)


def _cli_search(query: str, *, max_results: int, timeout_sec: float) -> ToolResultEnvelope:
    return _cli_call(
        "feishu.search",
        {"query": query, "resource_type": "doc", "max_results": max_results},
        timeout_sec=timeout_sec,
    )


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
            [{"title": title, "url": "https://feishu.cn/docx/mock_created", "snippet": "已创建；公司内获链接可读"}],
            tool=tool,
            meta={
                "url": "https://feishu.cn/docx/mock_created",
                "doc_token": "mock_created",
                "tenant_share": True,
            },
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

    if tool == "feishu.search" and _injected_search is not None:
        return _injected_search(
            str(args.get("query") or ""),
            max_results=int(args.get("max_results") or FEISHU_SEARCH.max_results),
            timeout_sec=to,
            user_access_token=user_access_token,
            open_id=open_id,
        )

    backend = flags.backend_name()
    if backend == "mock":
        return _mock_call(tool, args)

    if backend == "native" or (backend == "mcp" and not flags.mcp_endpoint()):
        # 无 MCP 侧车时走真实 OpenAPI，避免「开启了却全是 mcp_not_configured」
        from . import native as native_mod

        return native_mod.call_native(
            tool,
            args,
            user_access_token=user_access_token,
            open_id=open_id,
            timeout_sec=to,
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

    if backend == "cli":
        return _cli_call(tool, args, timeout_sec=to)

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
