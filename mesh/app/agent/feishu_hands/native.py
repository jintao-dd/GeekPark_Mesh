"""Feishu Hands · native OpenAPI（tenant / bot 身份）。

真实调用；需要 user_access_token 的能力会诚实失败（不编造、不抬权）。
当前会话 chat_id 可用于读群消息 / 机器人发消息。
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .. import feishu_api
from .normalize import envelope_fail, envelope_ok, normalize_docs
from ..tool_contract import ToolResultEnvelope

log = logging.getLogger("mesh.feishu_hands.native")

_TZ = timezone(timedelta(hours=8))


def _headers(*, user_access_token: str = "") -> dict[str, str]:
    if user_access_token:
        tok = user_access_token.strip()
    else:
        tok = feishu_api.get_tenant_access_token()
    return {
        "Authorization": f"Bearer {tok}",
        "Content-Type": "application/json; charset=utf-8",
    }


def _api(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    user_access_token: str = "",
    timeout: float = 15.0,
) -> dict[str, Any]:
    url = path if path.startswith("http") else f"https://open.feishu.cn{path}"
    r = requests.request(
        method,
        url,
        headers=_headers(user_access_token=user_access_token),
        json=payload,
        params=params,
        timeout=timeout,
    )
    try:
        data = r.json() if r.content else {}
    except Exception:
        raise RuntimeError(f"feishu_api_non_json:{r.status_code}:{r.text[:120]}")
    if not isinstance(data, dict):
        raise RuntimeError(f"feishu_api_bad_json:{r.status_code}")
    code = int(data.get("code") or 0)
    if r.status_code >= 400 or code != 0:
        msg = str(data.get("msg") or data or r.text[:200])
        raise RuntimeError(f"feishu_api_{code}:{msg[:160]}")
    return data


def _fail(tool: str, err: Exception | str) -> ToolResultEnvelope:
    s = str(err)
    if s.startswith("feishu_api_"):
        return envelope_fail(s[:200], tool=tool)
    return envelope_fail(f"native_error:{type(err).__name__ if isinstance(err, Exception) else 'err'}:{s[:120]}", tool=tool)


def _search_docs(query: str, *, user_access_token: str, max_results: int) -> ToolResultEnvelope:
    # 官方文档搜索要求 user_access_token；有则用，无则诚实失败
    if not user_access_token:
        return envelope_fail("user_token_required:doc_search", tool="feishu.search")
    data = _api(
        "POST",
        "/open-apis/suite/docs-api/search/object",
        payload={
            "search_key": query,
            "count": max(1, min(max_results, 50)),
            "offset": 0,
            "docs_types": ["doc", "docx", "sheet", "bitable", "file"],
        },
        user_access_token=user_access_token,
    )
    entities = (data.get("data") or {}).get("docs_entities") or []
    return envelope_ok(
        normalize_docs(entities if isinstance(entities, list) else [], kind="doc"),
        tool="feishu.search",
    )


def _search_wiki(query: str, *, user_access_token: str, max_results: int) -> ToolResultEnvelope:
    # wiki 节点搜索：优先 user；无 user 时尝试 tenant（可能无权限）
    try:
        data = _api(
            "POST",
            "/open-apis/wiki/v1/nodes/search",
            payload={"query": query, "count": max(1, min(max_results, 50))},
            user_access_token=user_access_token,
        )
    except Exception as e:
        return _fail("feishu.search", e)
    nodes = (data.get("data") or {}).get("items") or (data.get("data") or {}).get("nodes") or []
    items = []
    for n in nodes if isinstance(nodes, list) else []:
        if not isinstance(n, dict):
            continue
        token = str(n.get("node_token") or n.get("obj_token") or "")
        items.append(
            {
                "title": n.get("title") or n.get("name") or token,
                "snippet": str(n.get("obj_type") or "wiki"),
                "docs_token": token,
                "docs_type": "wiki",
                "url": f"https://feishu.cn/wiki/{token}" if token else "",
            }
        )
    return envelope_ok(normalize_docs(items, kind="wiki"), tool="feishu.search")


def _search_messages(
    query: str,
    *,
    chat_id: str,
    max_results: int,
) -> ToolResultEnvelope:
    cid = (chat_id or "").strip()
    if not cid:
        return envelope_fail("chat_id_required:message_search", tool="feishu.search")
    try:
        data = _api(
            "GET",
            "/open-apis/im/v1/messages",
            params={
                "container_id_type": "chat",
                "container_id": cid,
                "page_size": min(50, max(max_results * 3, 20)),
                "sort_type": "ByCreateTimeDesc",
            },
        )
    except Exception as e:
        return _fail("feishu.search", e)
    items_raw = (data.get("data") or {}).get("items") or []
    q = (query or "").strip().lower()
    out = []
    for m in items_raw if isinstance(items_raw, list) else []:
        if not isinstance(m, dict):
            continue
        body = m.get("body") or {}
        content = str(body.get("content") or "")
        try:
            cj = json.loads(content) if content.startswith("{") else {"text": content}
            text = str(cj.get("text") or content)
        except Exception:
            text = content
        if q and q not in text.lower() and q not in str(m.get("sender") or {}).lower():
            continue
        out.append(
            {
                "title": (text[:40] or "消息"),
                "snippet": text[:300],
                "docs_type": "message",
                "id": m.get("message_id"),
            }
        )
        if len(out) >= max_results:
            break
    return envelope_ok(normalize_docs(out, kind="message"), tool="feishu.search")


def _list_chats(query: str, *, max_results: int) -> ToolResultEnvelope:
    try:
        data = _api(
            "GET",
            "/open-apis/im/v1/chats",
            params={"page_size": min(50, max(max_results, 20))},
        )
    except Exception as e:
        return _fail("feishu.search", e)
    items = (data.get("data") or {}).get("items") or []
    q = (query or "").strip().lower()
    out = []
    for c in items if isinstance(items, list) else []:
        name = str(c.get("name") or "")
        # 空 query：列出机器人可见群；有 query：按名过滤
        if q and q not in name.lower() and q not in str(c.get("chat_id") or "").lower():
            continue
        out.append(
            {
                "title": name or c.get("chat_id"),
                "snippet": str(c.get("chat_id") or ""),
                "docs_type": "group",
                "id": c.get("chat_id"),
            }
        )
        if len(out) >= max_results:
            break
    return envelope_ok(normalize_docs(out, kind="group"), tool="feishu.search")


def _list_members(chat_id: str, *, max_results: int, query: str = "") -> ToolResultEnvelope:
    cid = (chat_id or "").strip()
    if not cid:
        return envelope_fail("chat_id_required_for_members", tool="feishu.search")
    try:
        data = _api(
            "GET",
            f"/open-apis/im/v1/chats/{cid}/members",
            params={
                "member_id_type": "open_id",
                "page_size": min(100, max(int(max_results), 20)),
            },
        )
    except Exception as e:
        return _fail("feishu.search", e)
    items = (data.get("data") or {}).get("items") or []
    q = (query or "").strip().lower()
    out = []
    for m in items if isinstance(items, list) else []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        oid = str(m.get("member_id") or m.get("open_id") or "").strip()
        if q and q not in name.lower() and q not in oid.lower():
            continue
        out.append(
            {
                "title": name or oid or "成员",
                "snippet": oid,
                "docs_type": "member",
                "id": oid,
                "url": "",
            }
        )
        if len(out) >= max_results:
            break
    return envelope_ok(normalize_docs(out, kind="member"), tool="feishu.search")


def _get_users(open_ids: list[str], *, max_results: int) -> ToolResultEnvelope:
    oids = [str(x or "").strip() for x in (open_ids or []) if str(x or "").strip()]
    if not oids:
        return envelope_fail("open_id_required_for_user", tool="feishu.search")
    out = []
    for oid in oids[: max(1, int(max_results))]:
        try:
            data = _api(
                "GET",
                f"/open-apis/contact/v3/users/{oid}",
                params={"user_id_type": "open_id"},
            )
        except Exception:
            continue
        user = (data.get("data") or {}).get("user") or {}
        if not isinstance(user, dict):
            continue
        name = str(user.get("name") or "").strip()
        job = str(user.get("job_title") or "").strip()
        emp = str(user.get("employee_no") or "").strip()
        email = str(user.get("enterprise_email") or "").strip()
        parts = [p for p in (job, emp, email, oid) if p]
        out.append(
            {
                "title": name or oid,
                "snippet": " · ".join(parts),
                "docs_type": "user",
                "id": oid,
                "url": "",
            }
        )
    if not out:
        return envelope_fail("user_lookup_empty", tool="feishu.search")
    return envelope_ok(normalize_docs(out, kind="user"), tool="feishu.search")


def call_native(
    tool: str,
    arguments: dict[str, Any],
    *,
    user_access_token: str = "",
    open_id: str = "",
    timeout_sec: float = 15.0,
) -> ToolResultEnvelope:
    args = dict(arguments or {})
    u = (user_access_token or "").strip()
    try:
        if tool == "feishu.search":
            rt = str(args.get("resource_type") or "doc").lower()
            q = str(args.get("query") or "").strip()
            mr = int(args.get("max_results") or 8)
            chat_id = str(args.get("chat_id") or "").strip()
            if rt in ("doc", "folder"):
                return _search_docs(q, user_access_token=u, max_results=mr)
            if rt == "wiki":
                return _search_wiki(q, user_access_token=u, max_results=mr)
            if rt == "message":
                return _search_messages(q, chat_id=chat_id, max_results=mr)
            if rt == "group":
                return _list_chats(q, max_results=mr)
            if rt == "member":
                return _list_members(
                    chat_id,
                    max_results=mr,
                    query=str(args.get("keyword") or q or ""),
                )
            if rt == "user":
                oids = [str(x).strip() for x in (args.get("open_ids") or []) if str(x).strip()]
                one = str(args.get("open_id") or args.get("user_id") or "").strip()
                if one:
                    oids = [one] + [x for x in oids if x != one]
                return _get_users(oids, max_results=mr)
            if rt == "calendar":
                return _calendar_list(q, days=14, max_results=mr)
            return envelope_fail(f"resource_type_unsupported:{rt}", tool=tool)

        if tool == "feishu.doc.get":
            return _doc_get(args, user_access_token=u)

        if tool == "feishu.calendar.list":
            return _calendar_list(
                str(args.get("query") or ""),
                days=int(args.get("days") or 7),
                max_results=12,
            )

        if tool == "feishu.discuss.summary":
            chat_id = str(args.get("chat_id") or "").strip()
            q = str(args.get("query") or args.get("person") or "").strip()
            env = _search_messages(q or " ", chat_id=chat_id, max_results=10)
            if env.ok and env.items:
                # 标成讨论摘要材料
                for it in env.items:
                    it["title"] = f"讨论：{it.get('title')}"
            return ToolResultEnvelope(
                ok=env.ok,
                tool=tool,
                source_tier=env.source_tier,
                truth_level=env.truth_level,
                items=list(env.items or []),
                error=env.error,
                empty=env.empty,
            )

        if tool == "feishu.doc.create":
            return _doc_create(args, user_access_token=u)

        if tool == "feishu.im.send":
            return _im_send(args)

        if tool == "feishu.calendar.create":
            return _calendar_create(args)

        return envelope_fail(f"unknown_tool:{tool}", tool=tool)
    except Exception as e:
        log.warning("native %s failed: %s", tool, e)
        return _fail(tool, e)


def _doc_get(args: dict[str, Any], *, user_access_token: str) -> ToolResultEnvelope:
    token = str(args.get("doc_token") or "").strip()
    url = str(args.get("url") or "").strip()
    if not token and url:
        m = re.search(r"/(docx|doc|wiki)/([a-zA-Z0-9]+)", url)
        if m:
            token = m.group(2)
    if not token:
        # 无 token：用 query 走搜索再取第一条（需 user token）
        q = str(args.get("query") or "").strip()
        if not q:
            return envelope_fail("doc_token_or_query_required", tool="feishu.doc.get")
        se = _search_docs(q, user_access_token=user_access_token, max_results=1)
        if not se.ok or not se.items:
            return se if not se.ok else envelope_fail("doc_not_found", tool="feishu.doc.get")
        return envelope_ok(se.items[:1], tool="feishu.doc.get")
    try:
        data = _api(
            "GET",
            f"/open-apis/docx/v1/documents/{token}",
            user_access_token=user_access_token,
        )
        meta = (data.get("data") or {}).get("document") or {}
        title = str(meta.get("title") or token)
        # 试拉纯文本块（失败则只返回标题）
        snippet = ""
        try:
            raw = _api(
                "GET",
                f"/open-apis/docx/v1/documents/{token}/raw_content",
                user_access_token=user_access_token,
            )
            snippet = str((raw.get("data") or {}).get("content") or "")[:500]
        except Exception:
            snippet = "（已定位文档，正文摘要暂不可用）"
        return envelope_ok(
            normalize_docs(
                [
                    {
                        "title": title,
                        "snippet": snippet,
                        "docs_token": token,
                        "docs_type": "docx",
                        "url": f"https://feishu.cn/docx/{token}",
                    }
                ]
            ),
            tool="feishu.doc.get",
        )
    except Exception as e:
        return _fail("feishu.doc.get", e)


def _docx_blocks_from_text(content: str) -> list[dict[str, Any]]:
    text = (content or "").strip() or " "
    # 简单：整篇一个 text run（API 限制长度时截断）
    chunk = text[:4000]
    return [
        {
            "block_type": 2,  # text
            "text": {
                "elements": [{"text_run": {"content": chunk}}],
                "style": {},
            },
        }
    ]


def _doc_create(args: dict[str, Any], *, user_access_token: str) -> ToolResultEnvelope:
    title = str(args.get("title") or "未命名文档").strip() or "未命名文档"
    content = str(args.get("content") or "")
    try:
        data = _api(
            "POST",
            "/open-apis/docx/v1/documents",
            payload={"title": title},
            user_access_token=user_access_token,  # 空则 tenant
        )
        doc = (data.get("data") or {}).get("document") or {}
        doc_id = str(doc.get("document_id") or "")
        if not doc_id:
            return envelope_fail("doc_create_empty_id", tool="feishu.doc.create")
        # 写入正文（尽力；失败仍返回已创建链接）
        if content.strip():
            try:
                _api(
                    "POST",
                    f"/open-apis/docx/v1/documents/{doc_id}/blocks/{doc_id}/children",
                    payload={"children": _docx_blocks_from_text(content)},
                    user_access_token=user_access_token,
                )
            except Exception as e:
                log.warning("doc body write failed: %s", e)
        url = f"https://feishu.cn/docx/{doc_id}"
        share_ok = False
        share_err = ""
        try:
            _set_tenant_link_share(doc_id, docs_type="docx", user_access_token=user_access_token)
            share_ok = True
        except Exception as e:
            share_err = str(e)[:160]
            log.warning("doc tenant share failed token=%s: %s", doc_id, e)
        snippet = "已创建；公司内获链接可读" if share_ok else "已创建（公司内链接权限未设上，请手动开「组织内获得链接可阅读」）"
        return envelope_ok(
            [{"title": title, "url": url, "snippet": snippet, "docs_token": doc_id}],
            tool="feishu.doc.create",
            meta={
                "url": url,
                "doc_token": doc_id,
                "tenant_share": share_ok,
                "tenant_share_error": share_err,
            },
        )
    except Exception as e:
        return _fail("feishu.doc.create", e)


def _set_tenant_link_share(
    token: str,
    *,
    docs_type: str = "docx",
    user_access_token: str = "",
) -> dict[str, Any]:
    """创建后默认：公司内部获得链接可阅读（tenant_readable）。docx / sheet / bitable 通用。"""
    dtype = (docs_type or "docx").strip() or "docx"
    return _api(
        "PATCH",
        f"/open-apis/drive/v2/permissions/{token}/public",
        params={"type": dtype},
        payload={"link_share_entity": "tenant_readable"},
        user_access_token=user_access_token,
    )


def open_tenant_readable(token: str, *, docs_type: str = "docx") -> dict[str, Any]:
    """对外：把已有云文档/表格设为组织内获链可读。"""
    return _set_tenant_link_share(token, docs_type=docs_type)


def _im_send(args: dict[str, Any]) -> ToolResultEnvelope:
    rid = str(args.get("receive_id") or "").strip()
    if not rid:
        return envelope_fail("receive_id_required", tool="feishu.im.send")
    text = str(args.get("text") or "").strip()
    if not text:
        return envelope_fail("text_required", tool="feishu.im.send")
    rtype = str(args.get("receive_id_type") or "chat_id").strip() or "chat_id"
    try:
        data = feishu_api.send_message(
            receive_id=rid,
            receive_id_type=rtype,
            msg_type="text",
            content={"text": text},
        )
        mid = str(data.get("message_id") or "")
        return envelope_ok(
            [{"title": "已发送", "snippet": text[:120], "docs_type": "message"}],
            tool="feishu.im.send",
            meta={"message_id": mid},
        )
    except Exception as e:
        return _fail("feishu.im.send", e)


def _primary_calendar_id() -> str:
    data = _api("GET", "/open-apis/calendar/v4/calendars", params={"page_size": 50})
    cals = (data.get("data") or {}).get("calendar_list") or []
    for c in cals if isinstance(cals, list) else []:
        if c.get("type") == "primary" or c.get("is_primary"):
            return str(c.get("calendar_id") or "")
        if str(c.get("calendar_id") or ""):
            # 退而求其次：第一个可写
            if c.get("permissions") in ("owner", "writer", None) or True:
                cid = str(c.get("calendar_id") or "")
                if cid:
                    return cid
    raise RuntimeError("no_calendar")


def _calendar_list(query: str, *, days: int, max_results: int) -> ToolResultEnvelope:
    try:
        cal_id = _primary_calendar_id()
        now = datetime.now(_TZ)
        start = now.isoformat()
        end = (now + timedelta(days=max(1, days))).isoformat()
        data = _api(
            "GET",
            f"/open-apis/calendar/v4/calendars/{cal_id}/events",
            params={
                "start_time": str(int(now.timestamp())),
                "end_time": str(int((now + timedelta(days=max(1, days))).timestamp())),
                "page_size": min(50, max(max_results, 10)),
            },
        )
        # 部分环境用 ISO；若失败上面会抛。兼容 events 字段
        events = (data.get("data") or {}).get("items") or []
        q = (query or "").strip().lower()
        out = []
        for ev in events if isinstance(events, list) else []:
            if not isinstance(ev, dict):
                continue
            summary = str((ev.get("summary") or ev.get("title") or "")).strip()
            if q and q not in summary.lower():
                continue
            st = ev.get("start_time") or ev.get("start") or {}
            et = ev.get("end_time") or ev.get("end") or {}
            snippet = f"{st.get('timestamp') or st.get('date') or ''} ~ {et.get('timestamp') or et.get('date') or ''}"
            out.append(
                {
                    "title": summary or "日程",
                    "snippet": snippet,
                    "docs_type": "event",
                    "token": ev.get("event_id"),
                }
            )
            if len(out) >= max_results:
                break
        return envelope_ok(normalize_docs(out, kind="calendar"), tool="feishu.calendar.list")
    except Exception as e:
        # 重试 ISO 查询参数变体
        try:
            cal_id = _primary_calendar_id()
            now = datetime.now(_TZ)
            data = _api(
                "POST",
                f"/open-apis/calendar/v4/calendars/{cal_id}/events/instance_view",
                payload={
                    "start_time": now.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
                    "end_time": (now + timedelta(days=max(1, days))).strftime(
                        "%Y-%m-%dT%H:%M:%S+08:00"
                    ),
                },
            )
            events = (data.get("data") or {}).get("items") or []
            out = []
            for ev in events if isinstance(events, list) else []:
                summary = str(ev.get("summary") or "日程")
                out.append({"title": summary, "snippet": str(ev.get("start_time") or ""), "docs_type": "event"})
                if len(out) >= max_results:
                    break
            return envelope_ok(normalize_docs(out, kind="calendar"), tool="feishu.calendar.list")
        except Exception as e2:
            return _fail("feishu.calendar.list", e2 if str(e2) else e)


def _calendar_create(args: dict[str, Any]) -> ToolResultEnvelope:
    title = str(args.get("title") or "未命名日程").strip()
    start = str(args.get("start") or "").strip()
    end = str(args.get("end") or "").strip()
    desc = str(args.get("description") or "").strip()
    if not start or not end:
        return envelope_fail("start_end_required", tool="feishu.calendar.create")

    def _ts(s: str) -> dict[str, str]:
        # 支持 ISO 或 epoch
        if re.fullmatch(r"\d{10,13}", s):
            sec = int(s[:10])
            return {"timestamp": str(sec), "timezone": "Asia/Shanghai"}
        # ISO
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_TZ)
            return {"timestamp": str(int(dt.timestamp())), "timezone": "Asia/Shanghai"}
        except Exception:
            return {"timestamp": s, "timezone": "Asia/Shanghai"}

    try:
        cal_id = _primary_calendar_id()
        data = _api(
            "POST",
            f"/open-apis/calendar/v4/calendars/{cal_id}/events",
            payload={
                "summary": title,
                "description": desc,
                "start_time": _ts(start),
                "end_time": _ts(end),
            },
        )
        ev = (data.get("data") or {}).get("event") or {}
        eid = str(ev.get("event_id") or "")
        return envelope_ok(
            [
                {
                    "title": title,
                    "snippet": f"{start} ~ {end}",
                    "docs_type": "event",
                    "token": eid,
                }
            ],
            tool="feishu.calendar.create",
            meta={"event_id": eid},
        )
    except Exception as e:
        return _fail("feishu.calendar.create", e)
