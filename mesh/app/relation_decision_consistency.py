"""Decision 一致性：relation_type / label / reason 不得互相矛盾。"""
from __future__ import annotations

import re

# LLM 可选 relation_type（Decision 阶段输出）
VALID_RELATION_TYPES = frozenset({
    "event_chain",
    "parallel_tracks",
    "info_complement",
    "one_sided",
    "external_watch",
    "overseas_link",
    "needs_review",
})

# label → 允许的 relation_type（须覆盖 FLAG_COLORS 全库）
_LABEL_TYPES: dict[str, frozenset[str]] = {
    "已联动": frozenset({"event_chain"}),
    "同一件事，两个部门各知一半": frozenset({"info_complement", "event_chain"}),
    "同一赛道，各自在做": frozenset({"parallel_tracks"}),
    "同一条赛道，各自在做": frozenset({"parallel_tracks"}),
    "同一公司，不同触点": frozenset({"parallel_tracks"}),
    "两个部门各有判断": frozenset({"parallel_tracks", "needs_review"}),
    "一方接触，另一方用得上": frozenset({"one_sided"}),
    "一方有需求，另一方尚未接触": frozenset({"one_sided"}),
    "一方接触了，另一方正在接触": frozenset({"one_sided", "event_chain"}),
    "一方报道了，另一方在接触": frozenset({"one_sided", "event_chain"}),
    "海外接触，国内可能承接": frozenset({"overseas_link", "event_chain"}),
    "海外新发现，国内尚未接触": frozenset({"overseas_link"}),
    "外部在热聊，我们还没碰": frozenset({"external_watch"}),
    "两处记录待核对": frozenset({"needs_review"}),
    "已公开报道，内部也在用": frozenset({"parallel_tracks", "info_complement"}),
    "采访对象也是客户": frozenset({"parallel_tracks", "event_chain"}),
    "中英文站同周各自成稿": frozenset({"parallel_tracks"}),
    "已排期，内容侧待安排": frozenset({"event_chain", "needs_review"}),
}

# LLM 偶发别名 → 规范标签（须在 FLAG_COLORS 内）
_LABEL_ALIASES: dict[str, str] = {
    "同一条赛道，各自在做": "同一赛道，各自在做",
}


def normalize_relation_label(label: str) -> str:
    label = (label or "").strip()
    return _LABEL_ALIASES.get(label, label)


# 单边/路由类：evidence 可仅一方团队（另一方为 → 建议团队）
_ONE_SIDED_RELATION_TYPES = frozenset({"one_sided", "overseas_link"})
_ONE_SIDED_LABEL_HINTS = (
    "一方接触", "一方有需求", "另一方尚未接触", "另一方在接触", "一方报道了",
    "海外接触", "海外新发现", "用得上", "国内可能承接", "国内尚未接触",
)

# relation_type → reason 中不应出现的矛盾短语
# parallel 本来就表示「非同一事链」；禁止把「非同一事件/仅共现」当矛盾，否则会废掉 parallel 标签
_TYPE_CONTRADICTIONS: dict[str, tuple[str, ...]] = {
    "parallel_tracks": (
        "无关系", "并无关系", "没有关系", "完全无关", "毫无关系",
    ),
    "event_chain": (
        "无关系", "并无关系", "非同一事链", "非同一事件", "各自独立", "仅共现", "不构成事链",
    ),
    "info_complement": ("无关系", "并无关系", "完全无关"),
}

_LINKED_LABELS = frozenset({"已联动", "同一件事，两个部门各知一半", "一方接触了，另一方正在接触"})
_LINK_HINTS = (
    "联动", "事链", "同一活动", "同一对象", "同一事项", "同一事件", "采访", "成片",
    "承接", "串成", "形成", "从", "到", "产出", "对谈", "连续",
)


def infer_relation_type(label: str, reason: str) -> str:
    """LLM 未给 relation_type 时，由 label 推断。"""
    label = normalize_relation_label(label)
    for lb, types in _LABEL_TYPES.items():
        if label == lb:
            return sorted(types)[0]
    if "各自在做" in label or "同一赛道" in label:
        return "parallel_tracks"
    if "已联动" in label:
        return "event_chain"
    if "各知一半" in label:
        return "info_complement"
    return "needs_review"


def check_decision_consistency(
    *,
    label: str,
    reason: str,
    relation_type: str | None = None,
) -> str | None:
    """不一致则返回错误说明；一致返回 None。"""
    label = normalize_relation_label(label)
    reason = (reason or "").strip()
    if not label or not reason:
        return "label 或 reason 为空"

    rtype = (relation_type or "").strip() or infer_relation_type(label, reason)
    if rtype not in VALID_RELATION_TYPES:
        return f"invalid_relation_type:{rtype}"

    allowed = _LABEL_TYPES.get(label)
    if allowed and rtype not in allowed:
        return f"relation_type与label矛盾:{rtype}↔{label}"

    for phrase in _TYPE_CONTRADICTIONS.get(rtype, ()):
        if phrase in reason:
            return f"reason与relation_type矛盾:{phrase}"

    if label in _LINKED_LABELS or rtype == "event_chain":
        if not any(h in reason for h in _LINK_HINTS):
            return "已联动/事链类label缺少明确事件或链路依据"

    # label 层：同一赛道类 reason 不得写「无关系」（「非同一事件」合法）
    if "各自在做" in label or "同一赛道" in label:
        for phrase in _TYPE_CONTRADICTIONS["parallel_tracks"]:
            if phrase in reason:
                return f"label与reason矛盾:{phrase}"

    return None


def normalize_decision(d: dict) -> dict:
    """补全 relation_type / decision_tier 并规范化 decision 字段。"""
    from .relation_display import normalize_decision_tier

    out = dict(d)
    label = normalize_relation_label((out.get("label") or "").strip())
    out["label"] = label
    reason = (out.get("reason") or "").strip()
    decision = (out.get("decision") or "").strip().lower()
    rtype = (out.get("relation_type") or "").strip()
    if not rtype and label:
        out["relation_type"] = infer_relation_type(label, reason)
    tier = normalize_decision_tier(
        decision, label, out.get("decision_tier"), relation_type=out.get("relation_type"),
    )
    out["decision_tier"] = tier
    if tier == "skip" and decision == "keep":
        out["decision"] = "skip"
    return out
