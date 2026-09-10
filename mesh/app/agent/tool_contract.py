"""Tool Contract — Colleague Brain 看到的统一工具表面。

Brain 不直接依赖 MCP/CLI 细节；只消费 Contract + 规范化结果。
飞书 live 与 Published 事实必须分 tier，禁止混级。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class SourceTier(str, Enum):
    PUBLISHED = "published"
    FEISHU_LIVE = "feishu_live"
    MODEL = "model"


class TruthLevel(str, Enum):
    ENTERPRISE_FACT = "enterprise_fact"
    LIVE_CONTEXT = "live_context"
    GENERAL_KNOWLEDGE = "general_knowledge"


class SideEffect(str, Enum):
    NONE = "none"
    WRITE = "write"


# source_tier → 允许的 truth_level（死契约）
_TIER_TRUTH: dict[SourceTier, TruthLevel] = {
    SourceTier.PUBLISHED: TruthLevel.ENTERPRISE_FACT,
    SourceTier.FEISHU_LIVE: TruthLevel.LIVE_CONTEXT,
    SourceTier.MODEL: TruthLevel.GENERAL_KNOWLEDGE,
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
        if self.side_effect == SideEffect.NONE and self.confirmation_required:
            # 读工具不应要求确认（准备写入另议）
            pass

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_tier"] = self.source_tier.value
        d["truth_level"] = self.truth_level.value
        d["side_effect"] = self.side_effect.value
        return d


@dataclass
class ToolResultEnvelope:
    """工具回传给 Brain 的规范化包（非用户可见）。"""

    ok: bool
    tool: str
    source_tier: SourceTier
    truth_level: TruthLevel
    items: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""
    empty: bool = False

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
        }


def speech_hint(tier: SourceTier | str) -> str:
    """合成话术提示（给 Brain，不是给用户看协议）。"""
    t = _as_tier(tier)
    return {
        SourceTier.PUBLISHED: "周报里记录的是",
        SourceTier.FEISHU_LIVE: "飞书最近的讨论/文档则",
        SourceTier.MODEL: "我觉得（看法，非企业事实）",
    }[t]


# —— 首批登记（Phase 2 只开放 doc）——

FEISHU_SEARCH = ToolContract(
    name="feishu.search",
    description=(
        "在飞书中搜索。resource_type 首期仅开放 doc；"
        "返回 live_context，不得当作 published 企业事实。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "resource_type": {
                "type": "string",
                "enum": ["doc", "message", "group"],
                "description": "Phase2-3 运行时仅允许 doc",
            },
        },
        "required": ["query", "resource_type"],
    },
    permission_scope="doc.read",
    timeout_sec=8.0,
    max_results=8,
    source_tier=SourceTier.FEISHU_LIVE,
    truth_level=TruthLevel.LIVE_CONTEXT,
    output_schema={
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "snippet": {"type": "string"},
            "url": {"type": "string"},
            "permission_ok": {"type": "boolean"},
        },
    },
    side_effect=SideEffect.NONE,
    confirmation_required=False,
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
    confirmation_required=False,
)

FEISHU_SEARCH_ALLOWED_TYPES_PHASE2 = frozenset({"doc"})


def feishu_search_type_allowed(resource_type: str, *, phase: str = "2") -> bool:
    rt = (resource_type or "").strip().lower()
    if phase in ("2", "3"):
        return rt in FEISHU_SEARCH_ALLOWED_TYPES_PHASE2
    return rt in ("doc", "message", "group")


REGISTRY: dict[str, ToolContract] = {
    FEISHU_SEARCH.name: FEISHU_SEARCH,
    ASK_PUBLISHED.name: ASK_PUBLISHED,
}
