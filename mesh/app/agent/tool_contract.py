"""Tool Contract — Colleague Brain 统一工具表面（10 项 Hands 能力）。

Brain 不直接依赖 MCP/CLI；只消费 Contract + 规范化结果。
飞书 live 与 Published 禁止混级；写工具必须 confirmation_required。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SourceTier(str, Enum):
    PUBLISHED = "published"
    FEISHU_LIVE = "feishu_live"
    CRM_PRIOR = "crm_prior"
    MODEL = "model"
    WIKI_CONTEXT = "wiki_context"
    ONTOLOGY = "ontology"


class TruthLevel(str, Enum):
    ENTERPRISE_FACT = "enterprise_fact"
    LIVE_CONTEXT = "live_context"
    CRM_CONTEXT = "crm_context"
    GENERAL_KNOWLEDGE = "general_knowledge"
    COMPANY_CONTEXT = "company_context"
    COMPANY_STRUCTURE = "company_structure"


class SideEffect(str, Enum):
    NONE = "none"
    WRITE = "write"


_TIER_TRUTH: dict[SourceTier, TruthLevel] = {
    SourceTier.PUBLISHED: TruthLevel.ENTERPRISE_FACT,
    SourceTier.FEISHU_LIVE: TruthLevel.LIVE_CONTEXT,
    SourceTier.CRM_PRIOR: TruthLevel.CRM_CONTEXT,
    SourceTier.MODEL: TruthLevel.GENERAL_KNOWLEDGE,
    SourceTier.WIKI_CONTEXT: TruthLevel.COMPANY_CONTEXT,
    SourceTier.ONTOLOGY: TruthLevel.COMPANY_STRUCTURE,
}


def _as_tier(tier: SourceTier | str) -> SourceTier:
    if isinstance(tier, SourceTier):
        return tier
    return SourceTier(str(tier))


def _as_truth(truth: TruthLevel | str) -> TruthLevel:
    if isinstance(truth, TruthLevel):
        return truth
    return TruthLevel(str(truth))


def truth_for_tier(tier: SourceTier | str) -> TruthLevel:
    return _TIER_TRUTH[_as_tier(tier)]


def assert_tier_truth_pair(tier: SourceTier | str, truth: TruthLevel | str) -> None:
    t = _as_tier(tier)
    tr = _as_truth(truth)
    expected = _TIER_TRUTH[t]
    if tr != expected:
        raise ValueError(
            f"混级禁止: source_tier={t.value} 只能配 truth_level={expected.value}，"
            f"收到 {tr.value}"
        )


@dataclass(frozen=True)
class ToolContract:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission_scope: str
    timeout_sec: float
    max_results: int
    source_tier: SourceTier
    truth_level: TruthLevel
    output_schema: dict[str, Any]
    side_effect: SideEffect = SideEffect.NONE
    confirmation_required: bool = False

    def __post_init__(self) -> None:
        assert_tier_truth_pair(self.source_tier, self.truth_level)
        if self.side_effect == SideEffect.WRITE and not self.confirmation_required:
            raise ValueError(f"写工具必须 confirmation_required=True: {self.name}")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_tier"] = self.source_tier.value
        d["truth_level"] = self.truth_level.value
        d["side_effect"] = self.side_effect.value
        return d


@dataclass
class ToolResultEnvelope:
    ok: bool
    tool: str
    source_tier: SourceTier
    truth_level: TruthLevel
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    empty: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        assert_tier_truth_pair(self.source_tier, self.truth_level)
        if self.ok and not self.items and not self.error:
            self.empty = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "tool": self.tool,
            "source_tier": self.source_tier.value,
            "truth_level": self.truth_level.value,
            "items": list(self.items or []),
            "error": self.error,
            "empty": self.empty,
            "meta": dict(self.meta or {}),
        }


def speech_hint(tier: SourceTier | str) -> str:
    t = _as_tier(tier)
    return {
        SourceTier.PUBLISHED: "周报里记录的是",
        SourceTier.FEISHU_LIVE: "飞书最近的讨论/文档则",
        SourceTier.CRM_PRIOR: "硅谷 CRM（思琪侧跟进）里",
        SourceTier.MODEL: "我觉得（看法，非企业事实）",
        SourceTier.WIKI_CONTEXT: "按我们公司语境",
        SourceTier.ONTOLOGY: "按组织/结构",
    }[t]


_LIVE_OUT = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "snippet": {"type": "string"},
        "url": {"type": "string"},
        "permission_ok": {"type": "boolean"},
    },
}

FEISHU_SEARCH = ToolContract(
    name="feishu.search",
    description=(
        "统一飞书搜索。resource_type: doc|message|group|wiki|folder|calendar|member|user|directory。"
        "member=当前群成员；user=按已知 open_id 查人；directory=应用通讯录权限范围内按姓名/工号查全员。"
        "返回 live_context，不得当作 published。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "resource_type": {
                "type": "string",
                "enum": [
                    "doc",
                    "message",
                    "group",
                    "wiki",
                    "folder",
                    "calendar",
                    "member",
                    "user",
                    "directory",
                ],
            },
        },
        "required": ["query", "resource_type"],
    },
    permission_scope="feishu.search",
    timeout_sec=8.0,
    max_results=8,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.NONE,
)

FEISHU_DOC_GET = ToolContract(
    name="feishu.doc.get",
    description="读取一篇飞书文档要点（标题/摘要/链接），只读。",
    input_schema={
        "type": "object",
        "properties": {
            "doc_token": {"type": "string"},
            "url": {"type": "string"},
            "query": {"type": "string"},
        },
    },
    permission_scope="doc.read",
    timeout_sec=10.0,
    max_results=1,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.NONE,
)

FEISHU_CALENDAR_LIST = ToolContract(
    name="feishu.calendar.list",
    description="列出近期日历日程（live_context）。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "days": {"type": "integer"},
        },
    },
    permission_scope="calendar.read",
    timeout_sec=8.0,
    max_results=12,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.NONE,
)

FEISHU_DISCUSS_SUMMARY = ToolContract(
    name="feishu.discuss.summary",
    description=(
        "某人/某群最近讨论摘要：底层走 message 检索 + 规范化条目；"
        "Brain 成文，不另造事实。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "person": {"type": "string"},
            "chat_id": {"type": "string"},
        },
        "required": ["query"],
    },
    permission_scope="im.read",
    timeout_sec=10.0,
    max_results=10,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.NONE,
)

FEISHU_DOC_CREATE = ToolContract(
    name="feishu.doc.create",
    description="创建飞书文档。须用户确认；受 MESH_FEISHU_HANDS_WRITE 约束。",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "content": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["title", "content", "confirmed"],
    },
    permission_scope="doc.write",
    timeout_sec=15.0,
    max_results=1,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.WRITE,
    confirmation_required=True,
)

FEISHU_IM_SEND = ToolContract(
    name="feishu.im.send",
    description="向指定人/群发送消息。须确认目标 + 正文；受 WRITE 开关约束。",
    input_schema={
        "type": "object",
        "properties": {
            "receive_id": {"type": "string"},
            "receive_id_type": {"type": "string"},
            "text": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["receive_id", "text", "confirmed"],
    },
    permission_scope="im.write",
    timeout_sec=10.0,
    max_results=1,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.WRITE,
    confirmation_required=True,
)

FEISHU_CALENDAR_CREATE = ToolContract(
    name="feishu.calendar.create",
    description="创建日程。须确认时间与标题；受 WRITE 开关约束。",
    input_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "description": {"type": "string"},
            "confirmed": {"type": "boolean"},
        },
        "required": ["title", "start", "end", "confirmed"],
    },
    permission_scope="calendar.write",
    timeout_sec=12.0,
    max_results=1,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.WRITE,
    confirmation_required=True,
)

FEISHU_CALENDAR_PROPOSE = ToolContract(
    name="feishu.calendar.propose",
    description=(
        "受控多步：拉当前群成员 + 查 busy → 推荐共同空档。"
        "不写日程；创建仍走 feishu.calendar.create + 确认。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "chat_id": {"type": "string"},
            "days": {"type": "integer"},
            "duration_min": {"type": "integer"},
        },
        "required": [],
    },
    permission_scope="calendar.read",
    timeout_sec=45.0,
    max_results=8,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema=_LIVE_OUT,
    side_effect=SideEffect.NONE,
)

ASK_PUBLISHED = ToolContract(
    name="ask.published",
    description="查询已上线周报企业事实（Evidence）。",
    input_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
    permission_scope="ask.published",
    timeout_sec=60.0,
    max_results=12,
    source_tier=SourceTier.PUBLISHED,
    truth_level=TruthLevel.ENTERPRISE_FACT,
    output_schema={"type": "object"},
    side_effect=SideEffect.NONE,
)

CRM_SEARCH = ToolContract(
    name="crm.search",
    description="查询硅谷 CRM（思琪/Lilyann Notion 底库：人/公司/沟通/Takes）。",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "mode": {
                "type": "string",
                "enum": ["auto", "person", "company", "recent", "take", "stats", "cross"],
            },
            "metric": {
                "type": "string",
                "enum": ["people_archive", "people_touched", "companies", "takes"],
            },
            "cross_op": {
                "type": "string",
                "enum": ["both", "crm_only", "weekly_only"],
            },
            "date_from": {"type": "string"},
            "date_to": {"type": "string"},
        },
        "required": ["query"],
    },
    permission_scope="crm.search",
    timeout_sec=20.0,
    max_results=12,
    source_tier=SourceTier.CRM_PRIOR,
    truth_level=TruthLevel.CRM_CONTEXT,
    output_schema={"type": "object"},
    side_effect=SideEffect.NONE,
)

# 生成成文 = Brain speak，无独立 Tool（#7）
SPEAK_GENERATE_NOTE = "colleague.speak — 整理成文/写稿，不写飞书；无需 Hands。"

FEISHU_SEARCH_ALLOWED_TYPES = frozenset(
    {
        "doc",
        "message",
        "group",
        "wiki",
        "folder",
        "calendar",
        "member",
        "user",
        "directory",
    }
)
# 兼容旧名
FEISHU_SEARCH_ALLOWED_TYPES_PHASE2 = frozenset({"doc"})

FEISHU_READ_TOOLS = frozenset(
    {
        FEISHU_SEARCH.name,
        FEISHU_DOC_GET.name,
        FEISHU_CALENDAR_LIST.name,
        FEISHU_CALENDAR_PROPOSE.name,
        FEISHU_DISCUSS_SUMMARY.name,
    }
)
FEISHU_WRITE_TOOLS = frozenset(
    {
        FEISHU_DOC_CREATE.name,
        FEISHU_IM_SEND.name,
        FEISHU_CALENDAR_CREATE.name,
    }
)
FEISHU_ALL_TOOLS = FEISHU_READ_TOOLS | FEISHU_WRITE_TOOLS


def feishu_search_type_allowed(resource_type: str, *, phase: str = "full") -> bool:
    rt = (resource_type or "").strip().lower()
    if phase in ("2",):
        return rt in FEISHU_SEARCH_ALLOWED_TYPES_PHASE2
    return rt in FEISHU_SEARCH_ALLOWED_TYPES


REGISTRY: dict[str, ToolContract] = {
    c.name: c
    for c in (
        FEISHU_SEARCH,
        FEISHU_DOC_GET,
        FEISHU_CALENDAR_LIST,
        FEISHU_CALENDAR_PROPOSE,
        FEISHU_DISCUSS_SUMMARY,
        FEISHU_DOC_CREATE,
        FEISHU_IM_SEND,
        FEISHU_CALENDAR_CREATE,
        ASK_PUBLISHED,
        CRM_SEARCH,
    )
}
