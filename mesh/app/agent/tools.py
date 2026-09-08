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
        "我是 Mesh Agent v1（只读已上线周报）。\n"
        "可以：查看帮助、列出已上线期次、问已上线内容、问团队关系摘要。\n"
        "不能：读草稿/原文、发布、改权限、多步自主调用。\n"
        f"当前身份状态：{identity.status}；Query Scope：{permission.query_scope}。"
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


def _default_ask_published(con, context: AgentContext, args: dict[str, Any]) -> ToolResult:
    """无 LLM 的 Harness 桩：只证明契约；真实部署可注入 ask_engine 适配器。"""
    slug = context.issue_ref.slug or ""
    q = str(args.get("q") or context.text or "").strip()
    if not slug:
        return ToolResult(
            ok=True,
            tool_id="ask.published",
            payload={"answer": "当前没有可引用的已上线期次。", "mode": "none"},
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim="无可用 IssueRef",
                    evidence_refs=[],
                    status="unsupported",
                    reason="no_issue_ref",
                )
            ],
        )
    # 确认期次仍 published
    row = con.execute(
        "SELECT status, published_json FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row or row["status"] != "published" or not (row["published_json"] or "").strip():
        return _deny("ask.published", "issue_not_published")

    ev = [f"ev:published:{slug}:stub"]
    answer = f"[harness] 已在已上线期次 {slug} 范围内检索：{q[:120]}"
    focus = (context.query_scope or {}).get("team_focus")
    if focus:
        answer += f"（视角：{focus}）"
    return ToolResult(
        ok=True,
        tool_id="ask.published",
        payload={"answer": answer, "mode": "harness_stub", "issue": slug},
        evidence_refs=ev,
        claim_bindings=[
            ClaimBinding(
                claim=answer,
                evidence_refs=ev,
                status="weak",
                reason="harness_stub_not_llm",
            )
        ],
    )


def _default_ask_relations(con, context: AgentContext, args: dict[str, Any]) -> ToolResult:
    slug = context.issue_ref.slug or ""
    if not slug:
        return ToolResult(
            ok=True,
            tool_id="ask.relations_summary",
            payload={"answer": "没有可引用的已上线期次，无法汇总关系。", "relations": []},
            evidence_refs=[],
            claim_bindings=[
                ClaimBinding(
                    claim="无 IssueRef",
                    evidence_refs=[],
                    status="unsupported",
                    reason="no_issue_ref",
                )
            ],
        )
    row = con.execute(
        "SELECT status, published_json FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row or row["status"] != "published":
        return _deny("ask.relations_summary", "issue_not_published")

    # 只读 published_json 内 relation 段（若有）；失败则空摘要
    import json

    relations: list[dict] = []
    try:
        data = json.loads(row["published_json"] or "{}")
        for key in ("relations", "关系", "relation_cards"):
            if isinstance(data.get(key), list):
                relations = data[key][:20]
                break
        if not relations and isinstance(data.get("sections"), dict):
            rel = data["sections"].get("relations") or data["sections"].get("关系")
            if isinstance(rel, list):
                relations = rel[:20]
    except Exception:
        relations = []

    ev = [f"ev:relation:{slug}:{i}" for i in range(min(3, len(relations)))] or [
        f"ev:relation:{slug}:empty"
    ]
    answer = f"[harness] 期次 {slug} 关系摘要：{len(relations)} 条（Published）。"
    return ToolResult(
        ok=True,
        tool_id="ask.relations_summary",
        payload={"answer": answer, "relations": relations, "issue": slug},
        evidence_refs=ev,
        claim_bindings=[
            ClaimBinding(
                claim=answer,
                evidence_refs=ev,
                status="grounded" if relations else "weak",
                reason="published_relations_summary",
            )
        ],
    )


# 可注入适配器（生产接 ask_engine；测试可替换）
AskAdapter = Callable[[Any, AgentContext, dict[str, Any]], ToolResult]
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
    adapter = _ask_published_adapter or _default_ask_published
    return adapter(con, context, args)


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
    adapter = _ask_relations_adapter or _default_ask_relations
    return adapter(con, context, args)


_REGISTRY: dict[str, Callable[..., ToolResult]] = {
    "system.help": tool_help,
    "context.list_issues": tool_list_issues,
    "ask.published": tool_ask_published,
    "ask.relations_summary": tool_ask_relations,
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
