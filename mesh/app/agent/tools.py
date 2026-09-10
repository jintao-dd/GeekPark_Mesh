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


def tool_feishu_search(
    con,
    identity: IdentityResult,
    permission: PermissionDecision,
    context: AgentContext,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    """feishu.search — live_context；不进 Published 事实池。"""
    args = args or {}
    blocked = _guard_common("feishu.search", identity, permission, context, args=args)
    if blocked:
        return blocked

    from . import feishu_hands
    from .tool_contract import SourceTier, TruthLevel, speech_hint

    if not feishu_hands.hands_enabled():
        return _deny("feishu.search", "hands_disabled")

    query = str(args.get("q") or args.get("query") or "").strip()
    resource_type = str(args.get("resource_type") or "doc").strip() or "doc"
    user_token = str(args.get("user_access_token") or "").strip()

    env = feishu_hands.feishu_search(
        query,
        resource_type=resource_type,
        identity=identity,
        user_access_token=user_token,
        phase="2",
    )
    payload = {
        "source_tier": SourceTier.FEISHU_LIVE.value,
        "truth_level": TruthLevel.LIVE_CONTEXT.value,
        "speech_hint": speech_hint(SourceTier.FEISHU_LIVE),
        "resource_type": resource_type,
        "items": list(env.items or []),
        "empty": bool(env.empty),
        "n_hits": len(env.items or []),
    }
    if not env.ok:
        # 失败：不编造；Brain 合成时用诚实空话
        payload["error"] = env.error
        return ToolResult(
            ok=False,
            tool_id="feishu.search",
            payload=payload,
            error=env.error or "feishu_search_failed",
            denied=False,
        )
    # 空结果也 ok：让合成层说「没查到」
    lines = []
    refs = []
    for it in env.items or []:
        title = str(it.get("title") or "").strip()
        url = str(it.get("url") or "").strip()
        snip = str(it.get("snippet") or "").strip()
        bit = title
        if snip:
            bit += f" — {snip[:120]}"
        if url:
            bit += f" ({url})"
            refs.append(f"feishu_doc:{url}")
        lines.append(f"- {bit}")
    if lines:
        hint = speech_hint(SourceTier.FEISHU_LIVE)
        answer = f"【{hint}】\n" + "\n".join(lines)
    else:
        answer = "飞书文档这边这轮没查到相关结果。"
    payload["answer"] = answer
    return ToolResult(
        ok=True,
        tool_id="feishu.search",
        payload=payload,
        evidence_refs=refs[:12],
        claim_bindings=[],  # live 不进 enterprise claim
    )


_REGISTRY: dict[str, Callable[..., ToolResult]] = {
    "system.help": tool_help,
    "context.list_issues": tool_list_issues,
    "ask.published": tool_ask_published,
    "ask.relations_summary": tool_ask_relations,
    "feishu.search": tool_feishu_search,
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
