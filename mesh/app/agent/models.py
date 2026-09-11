"""Agent v1 契约对象（②～⑦）。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Identity status（§4.3）
STATUS_BOUND = "bound"
STATUS_BOUND_TEAM_MISSING = "bound_team_missing"
STATUS_BOUND_TEAM_CONFLICT = "bound_team_conflict"
STATUS_UNLINKED = "unlinked"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_OPEN_ID_MISMATCH = "open_id_mismatch"
STATUS_ANONYMOUS_WEB = "anonymous_web"

DATA_TOOLS = frozenset({
    "ask.published",
    "ask.relations_summary",
    "context.list_issues",
    "feishu.search",
    "feishu.doc.get",
    "feishu.calendar.list",
    "feishu.discuss.summary",
    "feishu.doc.create",
    "feishu.im.send",
    "feishu.calendar.create",
})
ALL_TOOLS = frozenset({
    "system.help",
    "context.list_issues",
    "ask.published",
    "ask.relations_summary",
    "feishu.search",
    "feishu.doc.get",
    "feishu.calendar.list",
    "feishu.discuss.summary",
    "feishu.doc.create",
    "feishu.im.send",
    "feishu.calendar.create",
})

INTENTS = frozenset({
    "help",
    "whoami",
    "casual",
    "clarify",
    "list_issues",
    "ask_relations",
    "ask_published",
    "feishu_search",
    "feishu_doc_get",
    "feishu_calendar_list",
    "feishu_discuss",
    "feishu_write",
    "refuse",
})


@dataclass
class AgentEnvelope:
    """入站信封：本地/HTTP Harness 用；飞书接线后只填同样字段。"""

    text: str = ""
    channel: str = "web"  # web | feishu_dm | feishu_group | harness
    feishu_open_id: str = ""
    mesh_user_id: int | None = None
    chat_id: str = ""
    thread_id: str = ""
    session_id: str = ""
    # Contact 映射结果（Harness 注入；真实飞书通讯录同步后由 Identity 填充）
    mapped_teams: list[str] = field(default_factory=list)
    contact_sync: str = "skipped_no_scope"
    # 可选显式请求（≠ 授权）
    explicit_team: str = ""
    explicit_issue: str = ""
    lock_issue: bool = False
    # 会话 pin（Harness 传入；生产由 store 按 scope_key 读写）
    pinned_issue: str = ""
    pinned_issue_epoch: int = 0
    # 允许注入已解析 Identity（单测）
    identity_override: dict[str, Any] | None = None
    # 本轮飞书 @mentions：[{key, open_id, name}]
    mentions: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IdentityResult:
    status: str
    mesh_user_id: int | None = None
    feishu_open_id: str = ""
    mesh_role: str = "viewer"
    primary_team: str | None = None
    mapped_teams: list[str] = field(default_factory=list)
    mesh_users_team: str | None = None
    team_source: str = "none"
    contact_sync: str = "skipped_no_scope"
    bind_state: str = "unlinked"
    channel: str = "web"
    chat_id: str = ""
    display_hint: str = ""
    person: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "mesh_user_id": self.mesh_user_id,
            "feishu_open_id": self.feishu_open_id,
            "mesh_role": self.mesh_role,
            "primary_team": self.primary_team,
            "mapped_teams": list(self.mapped_teams),
            "mesh_users_team": self.mesh_users_team,
            "team_source": self.team_source,
            "contact_sync": self.contact_sync,
            "bind_state": self.bind_state,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "display_hint": self.display_hint,
            "person": dict(self.person),
        }


@dataclass
class PermissionDecision:
    agent_access: bool
    tool_acl: list[str]
    data_visibility: dict[str, Any]
    query_scope: dict[str, Any]
    deny_reason: str = ""
    published_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_access": self.agent_access,
            "tool_acl": list(self.tool_acl),
            "data_visibility": dict(self.data_visibility),
            "query_scope": dict(self.query_scope),
            "deny_reason": self.deny_reason,
            "published_only": True,
        }


@dataclass
class IssueRef:
    mode: str  # explicit | pinned | latest_published | none
    slug: str = ""
    locked: bool = False
    epoch: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "slug": self.slug,
            "locked": self.locked,
            "epoch": self.epoch,
            "reason": self.reason,
        }

@dataclass
class AgentContext:
    scope_key: str
    channel: str
    chat_id: str = ""
    thread_id: str = ""
    session_id: str = ""
    query_scope: dict[str, Any] = field(default_factory=dict)
    issue_ref: IssueRef = field(default_factory=lambda: IssueRef(mode="none"))
    chat_team: str = ""  # 群绑定；不改 Person.primary_team
    text: str = ""
    mentions: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope_key": self.scope_key,
            "channel": self.channel,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "session_id": self.session_id,
            "query_scope": dict(self.query_scope),
            "issue_ref": self.issue_ref.to_dict(),
            "chat_team": self.chat_team,
            "text": self.text,
            "mentions": list(self.mentions or []),
        }


@dataclass
class ClaimBinding:
    claim: str
    evidence_refs: list[str]
    status: str  # grounded | weak | unsupported
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim": self.claim,
            "evidence_refs": list(self.evidence_refs),
            "status": self.status,
            "reason": self.reason,
        }


@dataclass
class ToolResult:
    ok: bool
    tool_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    evidence_refs: list[str] = field(default_factory=list)
    claim_bindings: list[ClaimBinding] = field(default_factory=list)
    error: str = ""
    denied: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool_id": self.tool_id,
            "payload": dict(self.payload),
            "evidence_refs": list(self.evidence_refs),
            "claim_bindings": [c.to_dict() for c in self.claim_bindings],
            "error": self.error,
            "denied": self.denied,
        }


@dataclass
class AgentAnswer:
    text: str
    intent: str
    tools_called: list[str] = field(default_factory=list)
    fingerprint: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    claim_bindings: list[ClaimBinding] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    identity: dict[str, Any] = field(default_factory=dict)
    permission: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    refused: bool = False
    deny_reason: str = ""

    @property
    def data_tools_called(self) -> list[str]:
        return [t for t in self.tools_called if t in DATA_TOOLS]

    def to_dict(self) -> dict[str, Any]:
        display = ""
        if isinstance(self.trace, dict):
            display = str(self.trace.get("display_text") or "").strip()
        return {
            "text": self.text,
            # Feishu / 产品侧可见正文：Answer + 期次 + Claim 对齐 Evidence
            "display_text": display or self.text,
            "intent": self.intent,
            "tools_called": list(self.tools_called),
            "data_tools_called": self.data_tools_called,
            "fingerprint": self.fingerprint,
            "trace": dict(self.trace),
            "claim_bindings": [c.to_dict() for c in self.claim_bindings],
            "evidence_refs": list(self.evidence_refs),
            "identity": dict(self.identity),
            "permission": dict(self.permission),
            "context": dict(self.context),
            "refused": self.refused,
            "deny_reason": self.deny_reason,
        }
