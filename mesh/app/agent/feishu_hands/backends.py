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


def _cli_items_from_payload(payload: dict[str, Any]) -> list[Any]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(payload.get("items"), list):
        return list(payload.get("items") or [])
    if isinstance(data, list):
        return list(data)
    if isinstance(data, dict):
        for k in ("items", "events", "docs_entities", "messages", "calendar_list"):
            v = data.get(k)
            if isinstance(v, list):
                return list(v)
    return []


def _cli_subprocess_env(*, user_access_token: str = "") -> dict[str, str]:
    """Headless CLI env provider.

    lark-cli 1.0.x env provider 不会仅凭 APP_ID/SECRET 自动换 TAT；
    必须注入 LARKSUITE_CLI_TENANT_ACCESS_TOKEN，bot 身份才可用。
    """
    import os

    from .. import feishu_api

    env = {k: str(v) for k, v in os.environ.items() if v is not None}
    if not env.get("LARKSUITE_CLI_APP_ID") and env.get("FEISHU_APP_ID"):
        env["LARKSUITE_CLI_APP_ID"] = env["FEISHU_APP_ID"]
    if not env.get("LARKSUITE_CLI_APP_SECRET") and env.get("FEISHU_APP_SECRET"):
        env["LARKSUITE_CLI_APP_SECRET"] = env["FEISHU_APP_SECRET"]
    env.setdefault("LARKSUITE_CLI_BRAND", "feishu")
    uat = (user_access_token or env.get("LARKSUITE_CLI_USER_ACCESS_TOKEN") or "").strip()
    if uat:
        env["LARKSUITE_CLI_USER_ACCESS_TOKEN"] = uat
    if not (env.get("LARKSUITE_CLI_TENANT_ACCESS_TOKEN") or "").strip():
        try:
            env["LARKSUITE_CLI_TENANT_ACCESS_TOKEN"] = feishu_api.get_tenant_access_token()
        except Exception as e:
            log.warning("cli: mint tenant_access_token failed: %s", e)
    return env


def _cli_run(
    argv: list[str],
    *,
    timeout_sec: float,
    tool: str,
    as_identity: str = "bot",
    user_access_token: str = "",
    confirm_yes: bool = False,
) -> ToolResultEnvelope:
    bin_path = _cli_bin()
    cmd = [bin_path, *argv]
    identity = (as_identity or "bot").strip().lower()
    if identity not in ("bot", "user"):
        identity = "bot"
    if "--as" not in cmd:
        cmd.extend(["--as", identity])
    if "--format" not in cmd:
        cmd.extend(["--format", "json"])
    if confirm_yes and "--yes" not in cmd:
        cmd.append("--yes")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
            env=_cli_subprocess_env(user_access_token=user_access_token),
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
    err_payload: dict[str, Any] = {}
    try:
        data = json.loads(raw_out) if raw_out else {}
    except Exception:
        data = {}
    if not data and raw_err:
        try:
            err_payload = json.loads(raw_err)
        except Exception:
            err_payload = {}
    if proc.returncode != 0 or (isinstance(data, dict) and data.get("ok") is False):
        src = data if isinstance(data, dict) and data.get("ok") is False else err_payload
        err = _cli_error_message(src, raw_err, proc.returncode)
        log.warning("cli fail tool=%s identity=%s err=%s cmd=%s", tool, identity, err[:160], " ".join(cmd[1:6]))
        return envelope_fail(_cli_error_code(err, src), tool=tool)
    if not isinstance(data, dict):
        data = {"data": data}
    return envelope_ok([], tool=tool, meta={"cli": data})


def _cli_error_message(src: dict[str, Any], raw_err: str, returncode: int) -> str:
    err = ""
    if isinstance(src, dict):
        eobj = src.get("error") or {}
        if isinstance(eobj, dict):
            err = str(eobj.get("message") or eobj.get("hint") or "")
            code = eobj.get("code")
            if code is not None and str(code):
                err = f"feishu_api_{code}:{err}".rstrip(":")
        err = err or str(src.get("error") or "")
    return err or raw_err[:160] or f"cli_exit_{returncode}"


def _cli_error_code(err: str, src: dict[str, Any] | None = None) -> str:
    """Map CLI failures into codes Brain / last_block can classify."""
    s = (err or "").lower()
    raw = err or ""
    code = ""
    etype = ""
    if isinstance(src, dict):
        eobj = src.get("error") or {}
        if isinstance(eobj, dict):
            if eobj.get("code") is not None:
                code = str(eobj.get("code"))
            etype = str(eobj.get("type") or eobj.get("subtype") or "")
    if (
        "no access token" in s
        or "token_missing" in s
        or etype in ("authentication", "token_missing")
        or "user_token_required" in s
    ):
        return f"cli:auth_token:{raw[:140]}"
    if "only supports: user" in s or "only supports user" in s:
        return f"cli:user_identity_required:{raw[:140]}"
    if (
        "99991672" in raw
        or code == "99991672"
        or "scope" in s
        or "permission" in s
        or "access denied" in s
        or "权限" in raw
    ):
        return f"cli:scope_denied:{raw[:140]}"
    if "not_installed" in s or "cli_not_installed" in s:
        return "cli_not_installed"
    if "unknown flag" in s:
        return f"cli:bad_flag:{raw[:140]}"
    if raw.startswith("feishu_api_") or "feishu_api_" in raw:
        return f"cli:{raw[:180]}"
    return f"cli:{raw[:180]}"


def _cli_tenant_share(token: str, *, timeout_sec: float, user_access_token: str = "") -> tuple[bool, str]:
    """公司内获链可读：走 lark-cli drive permission.public.patch（已确认写路径的后续步）。"""
    tok = (token or "").strip()
    if not tok:
        return False, "missing_token"
    env = _cli_run(
        [
            "drive",
            "permission.public",
            "patch",
            "--token",
            tok,
            "--type",
            "docx",
            "--data",
            json.dumps({"link_share_entity": "tenant_readable"}, ensure_ascii=False),
        ],
        timeout_sec=timeout_sec,
        tool="feishu.doc.create",
        as_identity="bot",
        user_access_token=user_access_token,
        confirm_yes=True,
    )
    if env.ok:
        return True, ""
    return False, str(env.error or "share_failed")[:160]


def _cli_call(
    tool: str,
    arguments: dict[str, Any],
    *,
    timeout_sec: float,
    user_access_token: str = "",
) -> ToolResultEnvelope:
    """官方 lark-cli shortcuts（与 larksuite/cli Skills 同源命令面）。"""
    args = dict(arguments or {})
    uat = (user_access_token or str(args.get("user_access_token") or "")).strip()

    if tool == "feishu.search":
        rt = str(args.get("resource_type") or "doc").lower()
        q = str(args.get("query") or "").strip()
        mr = str(max(1, min(int(args.get("max_results") or 8), 20)))
        if rt in ("doc", "folder", "wiki"):
            # drive +search 支持 bot；docs +search 仅 user。优先 bot（env TAT）。
            doc_types = {
                "doc": "doc,docx,sheet,bitable,file",
                "folder": "folder",
                "wiki": "wiki",
            }.get(rt, "doc,docx,wiki")
            argv = [
                "drive",
                "+search",
                "--query",
                q,
                "--page-size",
                mr,
                "--doc-types",
                doc_types,
            ]
            env = _cli_run(
                argv,
                timeout_sec=timeout_sec,
                tool=tool,
                as_identity="bot",
                user_access_token=uat,
            )
            if not env.ok and uat:
                # 有 UAT 时再试 docs +search（user）
                env = _cli_run(
                    ["docs", "+search", "--query", q, "--page-size", mr],
                    timeout_sec=timeout_sec,
                    tool=tool,
                    as_identity="user",
                    user_access_token=uat,
                )
            if not env.ok:
                return env
            payload = (env.meta or {}).get("cli") or {}
            items = _cli_items_from_payload(payload if isinstance(payload, dict) else {})
            return envelope_ok(normalize_docs(items, kind=rt), tool=tool)
        if rt == "message":
            chat_id = str(args.get("chat_id") or "").strip()
            argv = ["im", "+messages-search", "--query", q, "--page-size", str(min(int(mr), 50))]
            if chat_id:
                argv += ["--chat-id", chat_id]
            env = _cli_run(
                argv,
                timeout_sec=timeout_sec,
                tool=tool,
                as_identity="bot",
                user_access_token=uat,
            )
            if not env.ok:
                return env
            payload = (env.meta or {}).get("cli") or {}
            items = _cli_items_from_payload(payload if isinstance(payload, dict) else {})
            return envelope_ok(normalize_docs(items, kind="message"), tool=tool)
        if rt == "calendar":
            return _cli_call(
                "feishu.calendar.list",
                {"query": q, "max_results": int(mr)},
                timeout_sec=timeout_sec,
                user_access_token=uat,
            )
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
        # fetch 通常 bot/user 均可；无 UAT 用 bot
        env = _cli_run(
            argv,
            timeout_sec=timeout_sec,
            tool=tool,
            as_identity="user" if uat else "bot",
            user_access_token=uat,
        )
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
        env = _cli_run(
            ["calendar", "+agenda"],
            timeout_sec=timeout_sec,
            tool=tool,
            as_identity="bot",
            user_access_token=uat,
        )
        if not env.ok:
            return env
        payload = (env.meta or {}).get("cli") or {}
        items = _cli_items_from_payload(payload if isinstance(payload, dict) else {})
        return envelope_ok(
            normalize_docs(items if isinstance(items, list) else [], kind="calendar"),
            tool=tool,
        )

    if tool == "feishu.discuss.summary":
        chat_id = str(args.get("chat_id") or "").strip()
        q = str(args.get("query") or args.get("person") or "").strip() or " "
        return _cli_call(
            "feishu.search",
            {"resource_type": "message", "query": q, "chat_id": chat_id, "max_results": 10},
            timeout_sec=timeout_sec,
            user_access_token=uat,
        )

    if tool == "feishu.doc.create":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        title = str(args.get("title") or "未命名文档")
        content = str(args.get("content") or "") or " "
        env = _cli_run(
            ["docs", "+create", "--title", title, "--doc-format", "markdown", "--content", content],
            timeout_sec=timeout_sec,
            tool=tool,
            as_identity="bot",
            user_access_token=uat,
        )
        if not env.ok:
            return env
        payload = (env.meta or {}).get("cli") or {}
        data = payload.get("data") if isinstance(payload, dict) else {}
        url = str((data or {}).get("url") or (data or {}).get("doc_url") or "")
        token = str(
            (data or {}).get("document_id")
            or (data or {}).get("doc_token")
            or (data or {}).get("token")
            or ""
        )
        if not url and token:
            url = f"https://feishu.cn/docx/{token}"
        share_ok = False
        share_err = ""
        if token:
            share_ok, share_err = _cli_tenant_share(
                token, timeout_sec=timeout_sec, user_access_token=uat
            )
            if not share_ok:
                log.warning("cli doc tenant share failed token=%s: %s", token, share_err)
        snippet = "已创建；公司内获链接可读" if share_ok else "已创建（公司内链接权限未设上）"
        return envelope_ok(
            [{"title": title, "url": url, "snippet": snippet, "docs_token": token}],
            tool=tool,
            meta={
                "url": url,
                "doc_token": token,
                "tenant_share": share_ok,
                "tenant_share_error": share_err,
                "backend": "cli",
            },
        )

    if tool == "feishu.im.send":
        if not args.get("confirmed"):
            return envelope_fail("confirmation_required", tool=tool)
        rid = str(args.get("receive_id") or args.get("chat_id") or "").strip()
        text = str(args.get("text") or "").strip()
        if not rid or not text:
            return envelope_fail("receive_id_and_text_required", tool=tool)
        # open_id 用 --user-id；chat 用 --chat-id
        if rid.startswith("ou_"):
            send_argv = ["im", "+messages-send", "--user-id", rid, "--text", text]
        else:
            send_argv = ["im", "+messages-send", "--chat-id", rid, "--text", text]
        env = _cli_run(
            send_argv,
            timeout_sec=timeout_sec,
            tool=tool,
            as_identity="bot",
            user_access_token=uat,
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
        env = _cli_run(
            argv,
            timeout_sec=timeout_sec,
            tool=tool,
            as_identity="bot",
            user_access_token=uat,
        )
        if not env.ok:
            return env
        return envelope_ok(
            [{"title": title, "snippet": f"{start} ~ {end}", "docs_type": "event"}],
            tool=tool,
        )

    return envelope_fail(f"cli_unknown_tool:{tool}", tool=tool)


def _cli_search(query: str, *, max_results: int, timeout_sec: float, user_access_token: str = "") -> ToolResultEnvelope:
    return _cli_call(
        "feishu.search",
        {"query": query, "resource_type": "doc", "max_results": max_results},
        timeout_sec=timeout_sec,
        user_access_token=user_access_token,
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
        return _cli_call(
            tool,
            args,
            timeout_sec=to,
            user_access_token=user_access_token,
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
