"""读者页展示 vs draft 全量 backlog。"""
from __future__ import annotations

from typing import Any

VALID_DECISION_TIERS = frozenset({"strong", "parallel", "watch", "skip"})

# label → 默认 tier（LLM 未给 decision_tier 时）
_LABEL_DEFAULT_TIER: dict[str, str] = {
    "已联动": "strong",
    "同一件事，两个部门各知一半": "strong",
    "一方接触了，另一方正在接触": "strong",
    "同一赛道，各自在做": "parallel",
    "同一条赛道，各自在做": "parallel",
    "同一公司，不同触点": "parallel",
    "两个部门各有判断": "parallel",
    "一方接触，另一方用得上": "watch",
    "一方有需求，另一方尚未接触": "watch",
    "海外接触，国内可能承接": "watch",
    "海外新发现，国内尚未接触": "watch",
    "外部在热聊，我们还没碰": "watch",
    "两处记录待核对": "watch",
    "已公开报道，内部也在用": "parallel",
    "采访对象也是客户": "parallel",
    "一方报道了，另一方在接触": "parallel",
    "中英文站同周各自成稿": "parallel",
    "已排期，内容侧待安排": "watch",
}


def infer_decision_tier(label: str, *, relation_type: str | None = None) -> str:
    label = (label or "").strip()
    if label in _LABEL_DEFAULT_TIER:
        return _LABEL_DEFAULT_TIER[label]
    if relation_type == "event_chain":
        return "strong"
    if relation_type == "parallel_tracks":
        return "parallel"
    if relation_type in ("external_watch", "one_sided", "needs_review"):
        return "watch"
    return "parallel"


def normalize_decision_tier(decision: str, label: str, tier: str | None, *, relation_type: str | None = None) -> str:
    decision = (decision or "").strip().lower()
    tier = (tier or "").strip().lower()
    if decision != "keep":
        return "skip"
    if tier in VALID_DECISION_TIERS and tier != "skip":
        return tier
    return infer_decision_tier(label, relation_type=relation_type)


def reader_visible(rel: dict) -> bool:
    """读者页展示：有证据与完整叙事的成卡均可展示（不再仅 strong）。"""
    if not isinstance(rel, dict):
        return False
    tier = (rel.get("decision_tier") or "").strip().lower()
    if tier == "skip":
        return False
    if not (rel.get("evidence") or []):
        return False
    return bool((rel.get("title") or "").strip() and (rel.get("body") or "").strip())


def split_relations_for_publish(relations: list[dict]) -> tuple[list[dict], list[dict]]:
    """(reader_relations, backlog_relations)"""
    reader: list[dict] = []
    backlog: list[dict] = []
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        vis = reader_visible(row)
        row["reader_visible"] = vis
        if vis:
            reader.append(row)
        else:
            backlog.append(row)
    return reader, backlog


def attach_reader_flags(relations: list[dict]) -> list[dict]:
    out: list[dict] = []
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        row = dict(r)
        row["reader_visible"] = reader_visible(row)
        out.append(row)
    return out


def display_summary(relations: list[dict]) -> dict[str, Any]:
    rels = attach_reader_flags(relations)
    reader, backlog = split_relations_for_publish(rels)
    by_tier: dict[str, int] = {}
    for r in rels:
        t = (r.get("decision_tier") or "unknown").strip().lower()
        by_tier[t] = by_tier.get(t, 0) + 1
    return {
        "n_total": len(rels),
        "n_reader": len(reader),
        "n_backlog": len(backlog),
        "by_tier": by_tier,
        "reader_titles": [(r.get("title") or "").strip() for r in reader],
        "backlog_titles": [(r.get("title") or "").strip() for r in backlog],
    }
