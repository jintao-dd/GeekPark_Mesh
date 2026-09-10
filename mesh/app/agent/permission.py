"""③ Permission：Tool ACL ⊥ Visibility ⊥ Query Scope。"""
from __future__ import annotations

from typing import Any

from .identity import is_business_team, normalize_team
from .models import (
    ALL_TOOLS,
    STATUS_BOUND,
    STATUS_BOUND_TEAM_CONFLICT,
    STATUS_BOUND_TEAM_MISSING,
    IdentityResult,
    PermissionDecision,
)

_HELP_ONLY = ["system.help"]
_BOUND_TOOLS = [
    "system.help",
    "context.list_issues",
    "ask.published",
    "ask.relations_summary",
]
# my_team 语义 Tool：v1 不进注册表，但 Permission 显式禁止
_FORBIDDEN_MY_TEAM = "ask.published.my_team"


def _bound_tools() -> list[str]:
    """Hands 开才把飞书工具放进 ACL。"""
    tools = list(_BOUND_TOOLS)
    try:
        from .feishu_hands import hands_enabled, write_enabled
        from .tool_contract import FEISHU_READ_TOOLS, FEISHU_WRITE_TOOLS

        if hands_enabled():
            for t in sorted(FEISHU_READ_TOOLS):
                if t not in tools:
                    tools.append(t)
            if write_enabled():
                for t in sorted(FEISHU_WRITE_TOOLS):
                    if t not in tools:
                        tools.append(t)
    except Exception:
        pass
    return tools


def decide_permission(
    identity: IdentityResult,
    *,
    explicit_team: str = "",
    chat_team: str = "",
) -> PermissionDecision:
    """本层不调检索。explicit_team ≠ 授权。"""
    status = identity.status
    published_vis = {
        "fact_surface": "published",
        "published_corps": "company",
        "forbid": ["draft", "raw", "unpublished"],
    }

    if status in (
        "unlinked",
        "ambiguous",
        "open_id_mismatch",
        "anonymous_web",
    ):
        return PermissionDecision(
            agent_access=True,  # 可进通道拿 help/引导
            tool_acl=list(_HELP_ONLY),
            data_visibility={**published_vis, "published_corps": "none"},
            query_scope={"mode": "none", "team_focus": None},
            deny_reason=f"identity:{status}",
            published_only=True,
        )

    if status == STATUS_BOUND_TEAM_CONFLICT:
        return PermissionDecision(
            agent_access=True,
            tool_acl=list(_HELP_ONLY),
            data_visibility={**published_vis, "published_corps": "none"},
            query_scope={"mode": "none", "team_focus": None},
            deny_reason="identity:bound_team_conflict",
            published_only=True,
        )

    # bound / bound_team_missing：公司 Published 可见；missing 无 my_team
    team_focus = None
    mode = "unfocused"
    deny = ""

    # Query Scope：explicit → chat_team → primary（仅作聚焦，非硬 ACL）
    et = normalize_team(explicit_team)
    ct = normalize_team(chat_team)
    if et:
        # Visibility：v1 bound 允许公司 Published，故 explicit team 可作为 focus
        team_focus = et
        mode = "explicit"
    elif ct:
        team_focus = ct
        mode = "chat"
    elif identity.primary_team and status == STATUS_BOUND:
        team_focus = identity.primary_team
        mode = "primary"
    elif status == STATUS_BOUND_TEAM_MISSING:
        mode = "unfocused"
        team_focus = None
        deny = "identity:bound_team_missing"

    if status == STATUS_BOUND_TEAM_MISSING:
        # 可 unfocused published；禁 my_team（不在 ACL 里）
        pass

    return PermissionDecision(
        agent_access=True,
        tool_acl=_bound_tools(),
        data_visibility=published_vis,
        query_scope={"mode": mode, "team_focus": team_focus},
        deny_reason=deny,
        published_only=True,
    )


def tool_allowed(permission: PermissionDecision, tool_id: str) -> bool:
    if tool_id == _FORBIDDEN_MY_TEAM:
        return False
    if tool_id not in ALL_TOOLS:
        return False
    return tool_id in (permission.tool_acl or [])


def assert_published_only(permission: PermissionDecision) -> None:
    if not permission.published_only:
        raise RuntimeError("published_only must be true")
    forbid = (permission.data_visibility or {}).get("forbid") or []
    for x in ("draft", "raw", "unpublished"):
        if x not in forbid:
            raise RuntimeError(f"visibility must forbid {x}")
