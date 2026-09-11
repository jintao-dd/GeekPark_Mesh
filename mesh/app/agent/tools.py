"""⑥ Tool 注册表：自防御；禁止绕过 Permission/Context 查库。"""
from __future__ import annotations

from typing import Any, Callable

from .models import (
    ALL_TOOLS,
    DATA_TOOLS,
    AgentContext,
    ClaimBinding,
    IdentityResult,
    PermissionDecision,
    ToolResult,
)
from .permission import assert_published_only, tool_allowed


class ToolDefenseError(Exception):
    pass


def _deny(tool_id: str, reason: str) -> ToolResult:
    return ToolResult(ok=False, tool_id=tool_id, denied=True, error=reason)


def _auth_required(tool_id: str, *, open_id: str, resource_type: str = "") -> ToolResult:
    from . import feishu_user_auth as uauth
    from .feishu_hands.identity_policy import capability_label

    cap = capability_label(tool_id, resource_type=resource_type)
    text = uauth.auth_guide_text(open_id=open_id, capability=cap)
    return ToolResult(
        ok=False,
        tool_id=tool_id,
        denied=False,
        error="user_auth_required",
        payload={
            "user_auth_required": True,
            "auth_text": text,
            "resource_type": resource_type,
            "n_hits": 0,
            "source_tier": "feishu_live",
        },
    )


def _resolve_uat(identity: IdentityResult, args: dict[str, Any] | None) -> str:
    from . import feishu_user_auth as uauth

    args = args or {}
    oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
    return uauth.resolve_uat(
        open_id=oid,
        explicit=str(args.get("user_access_token") or ""),
    )


def _ensure_identity(
    tool_id: str,
    identity: IdentityResult,
    args: dict[str, Any],
    *,
    resource_type: str = "",
) -> tuple[str, ToolResult | None]:
    """返回 (uat, deny_or_auth_result)。"""
    from .feishu_hands.identity_policy import IdentityNeed, need_for_tool

    uat = _resolve_uat(identity, args)
    need = need_for_tool(tool_id, resource_type=resource_type)
    oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
    if need == IdentityNeed.USER and not uat:
        return "", _auth_required(tool_id, open_id=oid, resource_type=resource_type)
    if need == IdentityNeed.USER_PREFERRED and not uat:
        # 日历：无 UAT 仍可 bot freebusy，但标注；若用户明确要「我的日程」则引导
        q = str(args.get("q") or args.get("query") or args.get("keyword") or "").strip()
        if any(x in q for x in ("我的日程", "我的日历", "agenda", "今天有什么会")):
            return "", _auth_required(tool_id, open_id=oid, resource_type=resource_type or "calendar")
    args["user_access_token"] = uat
    return uat, None


def _guard_common(
    tool_id: str,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    *,
    args: dict[str, Any] | None = None,
) -> ToolResult | None:
    """Tool 入口自检；失败返回 ToolResult，成功返回 None。"""
    args = args or {}
    assert_published_only(permission)

    if tool_id not in ALL_TOOLS:
        return _deny(tool_id, "unknown_tool")

    if not tool_allowed(permission, tool_id):
        return _deny(tool_id, "acl_denied")

    # 禁止调用方偷偷塞 draft/raw surface
    surface = str(args.get("fact_surface") or args.get("surface") or "published").lower()
    if surface in ("draft", "raw", "unpublished", "source", "sources"):
        return _deny(tool_id, "published_only")

    # 禁止绕过 IssueRef：args.issue 必须与 Context 一致（或为空用 Context）
    req_issue = str(args.get("issue") or args.get("slug") or "").strip()
    if req_issue:
        cref = context.issue_ref
        if cref.mode == "none" or not cref.slug:
            return _deny(tool_id, "issue_ref_bypass")
        if req_issue != cref.slug:
            return _deny(tool_id, "issue_ref_mismatch")

    # team scope 不得绕过 Permission.query_scope
    req_team = str(args.get("team") or args.get("team_focus") or "").strip()
    allowed_focus = (permission.query_scope or {}).get("team_focus")
    if req_team:
        if not allowed_focus or req_team != allowed_focus:
            # explicit 已写入 permission.query_scope 才可
            if (permission.query_scope or {}).get("mode") != "explicit":
                return _deny(tool_id, "team_scope_bypass")
            if req_team != allowed_focus:
                return _deny(tool_id, "team_scope_bypass")

    # conflict / unlinked 不应到达数据 Tool（双保险）
    if tool_id in DATA_TOOLS and identity.status in (
        "unlinked",
        "ambiguous",
        "open_id_mismatch",
        "anonymous_web",
        "bound_team_conflict",
    ):
        return _deny(tool_id, f"identity:{identity.status}")

    return None


def tool_help(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    blocked = _guard_common("system.help", identity, permission, context, args=args)
    if blocked:
        return blocked
    text = (
        "我是 Mesh，GeekPark 的周报助手。"
        "你问已上线周报里的人或事就行，不用学固定句式。"
    )
    return ToolResult(
        ok=True,
        tool_id="system.help",
        payload={"help": text},
        claim_bindings=[],
        evidence_refs=[],
    )


def tool_list_issues(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    blocked = _guard_common("context.list_issues", identity, permission, context, args=args)
    if blocked:
        return blocked
    rows = con.execute(
        "SELECT slug, period_label, date_end, status FROM issues "
        "WHERE status='published' AND published_json IS NOT NULL "
        "AND TRIM(published_json) != '' "
        "ORDER BY date_end DESC, id DESC LIMIT 50"
    ).fetchall()
    issues = [
        {
            "slug": r["slug"],
            "period_label": r["period_label"] or "",
            "date_end": r["date_end"] or "",
            "status": "published",
        }
        for r in rows
    ]
    # 防御：绝不返回 draft
    issues = [i for i in issues if i["status"] == "published"]
    refs = [f"issue:{i['slug']}" for i in issues[:20]]
    claim = ClaimBinding(
        claim=f"共 {len(issues)} 个已上线期次",
        evidence_refs=refs[:5],
        status="grounded" if issues else "unsupported",
        reason="list_published_issues",
    )
    return ToolResult(
        ok=True,
        tool_id="context.list_issues",
        payload={"issues": issues, "count": len(issues)},
        evidence_refs=refs,
        claim_bindings=[claim],
    )


def _call_published(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any],
) -> ToolResult:
    from . import adapters

    return adapters.ask_published(con, identity, permission, context, args)


def _call_relations(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any],
) -> ToolResult:
    from . import adapters

    return adapters.ask_relations_summary(con, identity, permission, context, args)


# 可注入适配器（契约测试可替换；默认走真实 Mesh）
AskAdapter = Callable[
    [Any, IdentityResult, PermissionDecision, AgentContext, dict[str, Any]],
    ToolResult,
]
_ask_published_adapter: AskAdapter | None = None
_ask_relations_adapter: AskAdapter | None = None


def set_ask_adapters(
    published: AskAdapter | None = None,
    relations: AskAdapter | None = None,
) -> None:
    global _ask_published_adapter, _ask_relations_adapter
    _ask_published_adapter = published
    _ask_relations_adapter = relations


def tool_ask_published(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    args = args or {}
    blocked = _guard_common("ask.published", identity, permission, context, args=args)
    if blocked:
        return blocked
    adapter = _ask_published_adapter or _call_published
    return adapter(con, identity, permission, context, args)


def tool_ask_relations(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    args = args or {}
    blocked = _guard_common(
        "ask.relations_summary", identity, permission, context, args=args
    )
    if blocked:
        return blocked
    adapter = _ask_relations_adapter or _call_relations
    return adapter(con, identity, permission, context, args)


def _feishu_tool_result(tool_id: str, env, *, empty_msg: str) -> ToolResult:
    from .tool_contract import SourceTier, TruthLevel, speech_hint

    payload = {
        "source_tier": SourceTier.FEISHU_LIVE.value,
        "truth_level": TruthLevel.LIVE_CONTEXT.value,
        "speech_hint": speech_hint(SourceTier.FEISHU_LIVE),
        "items": list(env.items or []),
        "empty": bool(env.empty),
        "n_hits": len(env.items or []),
        "meta": dict(getattr(env, "meta", None) or {}),
    }
    if not env.ok:
        payload["error"] = env.error
        return ToolResult(
            ok=False,
            tool_id=tool_id,
            payload=payload,
            error=env.error or f"{tool_id}_failed",
            denied=env.error in ("hands_disabled", "write_disabled", "acl_denied"),
        )
    lines = []
    refs = []
    for it in env.items or []:
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        snip = str(it.get("snippet") or "").strip()
        dtype = str(it.get("docs_type") or it.get("type") or "").lower()
        # 成员/用户：snippet 常是 open_id，不对同事展示
        if dtype in ("member", "user", "person") and (
            snip.startswith("ou_") or snip.startswith("oc_") or snip.startswith("on_")
        ):
            snip = ""
        if dtype in ("group", "chat") and snip.startswith("oc_"):
            snip = ""
        bit = title
        if snip:
            bit += f" — {snip[:160]}"
        if url:
            bit += f" ({url})"
            refs.append(f"feishu:{url}")
        lines.append(f"- {bit}")
    if lines:
        hint = speech_hint(SourceTier.FEISHU_LIVE)
        answer = f"【{hint}】\n" + "\n".join(lines)
    else:
        answer = empty_msg
    payload["answer"] = answer
    return ToolResult(
        ok=True,
        tool_id=tool_id,
        payload=payload,
        evidence_refs=refs[:12],
        claim_bindings=[],
    )


def _with_chat(args: dict[str, Any], context: AgentContext) -> dict[str, Any]:
    out = dict(args or {})
    cid = str(getattr(context, "chat_id", None) or "").strip()
    if cid:
        out.setdefault("chat_id", cid)
    return out


def tool_feishu_search(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    args = _with_chat(args or {}, context)
    blocked = _guard_common("feishu.search", identity, permission, context, args=args)
    if blocked:
        return blocked
    from . import feishu_hands

    if not feishu_hands.hands_enabled():
        return _deny("feishu.search", "hands_disabled")
    query = str(args.get("q") or args.get("query") or "").strip()
    resource_type = str(args.get("resource_type") or "doc").strip() or "doc"
    _uat, auth_block = _ensure_identity(
        "feishu.search", identity, args, resource_type=resource_type
    )
    if auth_block:
        return auth_block
    open_ids = [str(x).strip() for x in (args.get("open_ids") or []) if str(x).strip()]
    one = str(args.get("open_id") or args.get("user_id") or "").strip()
    if one and one not in open_ids:
        open_ids.insert(0, one)
    env = feishu_hands.feishu_search(
        query,
        resource_type=resource_type,
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
        chat_id=str(args.get("chat_id") or ""),
        phase="full",
        keyword=str(args.get("keyword") or "").strip(),
        open_ids=open_ids,
        user_open_id=one,
    )
    tr = _feishu_tool_result(
        "feishu.search",
        env,
        empty_msg=f"飞书 {resource_type} 这边这轮没查到相关结果。",
    )
    tr.payload["resource_type"] = resource_type
    return tr


def tool_feishu_doc_get(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common("feishu.doc.get", identity, permission, context, args=args)
    if blocked:
        return blocked
    from . import feishu_hands

    env = feishu_hands.doc_get(
        doc_token=str(args.get("doc_token") or ""),
        url=str(args.get("url") or ""),
        query=str(args.get("q") or args.get("query") or ""),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result("feishu.doc.get", env, empty_msg="这篇飞书文档这轮没读到要点。")


def tool_feishu_calendar_list(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common(
        "feishu.calendar.list", identity, permission, context, args=args
    )
    if blocked:
        return blocked
    from . import feishu_hands

    _uat, auth_block = _ensure_identity("feishu.calendar.list", identity, args)
    if auth_block:
        return auth_block
    env = feishu_hands.calendar_list(
        query=str(args.get("q") or args.get("query") or ""),
        days=int(args.get("days") or 7),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result(
        "feishu.calendar.list", env, empty_msg="近期日程这边没查到。"
    )


def tool_feishu_calendar_propose(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common(
        "feishu.calendar.propose", identity, permission, context, args=args
    )
    if blocked:
        return blocked
    from . import feishu_hands

    _uat, auth_block = _ensure_identity("feishu.calendar.propose", identity, args)
    if auth_block:
        return auth_block
    env = feishu_hands.calendar_propose(
        chat_id=str(args.get("chat_id") or ""),
        days=int(args.get("days") or 5),
        duration_min=int(args.get("duration_min") or 60),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result(
        "feishu.calendar.propose",
        env,
        empty_msg="这轮没算出共同空档。",
    )


def tool_feishu_discuss_summary(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common(
        "feishu.discuss.summary", identity, permission, context, args=args
    )
    if blocked:
        return blocked
    from . import feishu_hands

    env = feishu_hands.discuss_summary(
        query=str(args.get("q") or args.get("query") or ""),
        person=str(args.get("person") or ""),
        chat_id=str(args.get("chat_id") or ""),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result(
        "feishu.discuss.summary",
        env,
        empty_msg="飞书讨论这边没查到可摘要的内容。",
    )


def tool_feishu_doc_create(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common("feishu.doc.create", identity, permission, context, args=args)
    if blocked:
        return blocked
    from . import feishu_hands

    env = feishu_hands.doc_create(
        title=str(args.get("title") or "").strip() or "未命名文档",
        content=str(args.get("content") or args.get("q") or ""),
        confirmed=bool(args.get("confirmed")),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result(
        "feishu.doc.create", env, empty_msg="文档没有创建成功。"
    )


def tool_feishu_im_send(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common("feishu.im.send", identity, permission, context, args=args)
    if blocked:
        return blocked
    from . import feishu_hands

    rid = str(args.get("receive_id") or args.get("chat_id") or "").strip()
    env = feishu_hands.im_send(
        receive_id=rid,
        text=str(args.get("text") or args.get("q") or ""),
        receive_id_type=str(args.get("receive_id_type") or "chat_id"),
        confirmed=bool(args.get("confirmed")),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result("feishu.im.send", env, empty_msg="消息没有发出去。")


def tool_feishu_calendar_create(con, identity, permission, context, args=None):
    args = _with_chat(args or {}, context)
    blocked = _guard_common(
        "feishu.calendar.create", identity, permission, context, args=args
    )
    if blocked:
        return blocked
    from . import feishu_hands

    _uat, auth_block = _ensure_identity("feishu.calendar.create", identity, args)
    if auth_block:
        return auth_block
    env = feishu_hands.calendar_create(
        title=str(args.get("title") or "").strip() or "未命名日程",
        start=str(args.get("start") or ""),
        end=str(args.get("end") or ""),
        description=str(args.get("description") or ""),
        confirmed=bool(args.get("confirmed")),
        identity=identity,
        user_access_token=str(args.get("user_access_token") or ""),
    )
    return _feishu_tool_result(
        "feishu.calendar.create", env, empty_msg="日程没有创建成功。"
    )


_REGISTRY: dict[str, Callable[..., ToolResult]] = {
    "system.help": tool_help,
    "context.list_issues": tool_list_issues,
    "ask.published": tool_ask_published,
    "ask.relations_summary": tool_ask_relations,
    "feishu.search": tool_feishu_search,
    "feishu.doc.get": tool_feishu_doc_get,
    "feishu.calendar.list": tool_feishu_calendar_list,
    "feishu.calendar.propose": tool_feishu_calendar_propose,
    "feishu.discuss.summary": tool_feishu_discuss_summary,
    "feishu.doc.create": tool_feishu_doc_create,
    "feishu.im.send": tool_feishu_im_send,
    "feishu.calendar.create": tool_feishu_calendar_create,
}


def invoke_tool(
    tool_id: str,
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    """唯一 Tool 入口。Agent 不得旁路直查 DB。"""
    fn = _REGISTRY.get(tool_id)
    if not fn:
        return _deny(tool_id, "unknown_tool")
    return fn(con, identity, permission, context, args)


def try_invoke_forbidden(tool_id: str, *args: Any, **kwargs: Any) -> ToolResult:
    """测试用：尝试未注册 / 禁止 Tool。"""
    return invoke_tool(tool_id, *args, **kwargs)
