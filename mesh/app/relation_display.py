"""关系展示与 Publish 投影。

产品口径（2026-09 更正）：
  能进 draft_json.relations 的（有 evidence、能落到条目）= 要发给读者的。
  decision_tier（strong / parallel / watch）只影响**展示排序强度**，不决定可见性。
  skip 不保留。

`reader_visible` 是派生字段：卡完整（evidence + title + **非空 body**）且非 skip → 读者可见。
`build_published_projection(draft)` 是 Publish / Preview-reader 切片的唯一入口。
"""
from __future__ import annotations

import copy
from typing import Any

VALID_DECISION_TIERS = frozenset({"strong", "parallel", "watch", "skip"})
# 读者可见的 keep 档（强度仅用于排序）
READER_TIERS = frozenset({"strong", "parallel", "watch"})
# 兼容旧名：不再表示「不上读者页」
BACKLOG_TIERS = frozenset()

# Publish 投影时从 published_json 剥离的内部键（draft 可保留）
_INTERNAL_DRAFT_KEYS = frozenset({
    "_stale",
    "_relations_reader",
    "_relations_backlog",
    "_relation_decision_audit",
})

# 展示顺序：数值越小越靠前
_TIER_SORT_RANK: dict[str, int] = {
    "strong": 0,
    "parallel": 1,
    "watch": 2,
}

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


def _card_complete(rel: dict) -> bool:
    """质量优先：读者卡必须有 evidence + title + 非空关系总结 body。"""
    if not (rel.get("evidence") or []):
        return False
    if not (rel.get("title") or "").strip():
        return False
    if not (rel.get("body") or "").strip():
        return False
    return True


def dedupe_body_vs_details(rel: dict) -> dict:
    """展示用：body 与 details 重复时清空 body（不改原 dict）。"""
    from .relation_verify import body_redundant_with_details

    out = dict(rel)
    body = (out.get("body") or "").strip()
    details = list(out.get("details") or [])
    if body_redundant_with_details(body, details):
        out["body"] = ""
    return out


def tier_sort_key(rel: dict) -> tuple:
    """排序键：strong → parallel → watch → 其他；同档保持原相对顺序由稳定 sort 保证。"""
    tier = (rel.get("decision_tier") or "").strip().lower()
    return (_TIER_SORT_RANK.get(tier, 1),)


def sort_relations_by_strength(relations: list[dict] | None) -> list[dict]:
    """按强度排序；稳定排序保留同档原顺序。"""
    rels = [r for r in (relations or []) if isinstance(r, dict)]
    return sorted(rels, key=tier_sort_key)


def sort_relation_entries_by_strength(entries: list[dict]) -> list[dict]:
    """entries: [{index, rel}, ...] 按 rel 强度排序，保留原 relations 下标。"""
    return sorted(entries, key=lambda e: tier_sort_key(e.get("rel") or {}))


def indexed_relations_for_display(relations: list | None) -> list[dict]:
    """页面展示用：按强度排序，index 仍指向 data.relations 原下标（编辑路径不乱）。"""
    entries = []
    for i, r in enumerate(relations or []):
        if not isinstance(r, dict):
            continue
        entries.append({"index": i, "rel": dedupe_body_vs_details(r)})
    return sort_relation_entries_by_strength(entries)


def reader_visible(rel: dict) -> bool:
    """派生字段：进草稿且卡完整（有 evidence + title/body）→ 读者可见。

    decision_tier 只影响排序，不决定是否可见。skip / 不完整 → 不可见。
    """
    if not isinstance(rel, dict):
        return False
    tier = (rel.get("decision_tier") or "").strip().lower()
    if tier == "skip":
        return False
    return _card_complete(rel)


def is_reader_tier(rel: dict) -> bool:
    """兼容旧调用：非 skip 的 keep 档（含无 tier 旧稿）视为可上读者页的档。"""
    if not isinstance(rel, dict):
        return False
    tier = (rel.get("decision_tier") or "").strip().lower()
    if tier == "skip":
        return False
    if not tier:
        return True  # 旧稿无 tier
    return tier in READER_TIERS


def split_relations_for_publish(relations: list[dict]) -> tuple[list[dict], list[dict]]:
    """(reader_relations, incomplete_backlog) — 完整卡全部进读者；不完整进 backlog 仅供审计。"""
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
    reader = sort_relations_by_strength(reader)
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


def count_reader_relations(relations: list | None) -> int:
    """KPI / 读者卡口径：进草稿且完整的关系卡数（含 parallel/watch）。"""
    return sum(1 for r in (relations or []) if isinstance(r, dict) and reader_visible(r))


def draft_backlog_relations(relations: list | None) -> list[dict]:
    """仅不完整 / skip 残留（正常管线不应进草稿）。兼容旧调用，预览页不再展示积压区。"""
    out: list[dict] = []
    for i, r in enumerate(relations or []):
        if not isinstance(r, dict):
            continue
        if reader_visible(r):
            continue
        tier = (r.get("decision_tier") or "").strip().lower()
        if tier == "skip":
            continue
        row = dict(r)
        row["reader_visible"] = False
        out.append({"index": i, "rel": row})
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


def _sync_relation_kpi(data: dict, n_reader: int) -> None:
    kpis = list(data.get("kpis") or [])
    found = False
    for k in kpis:
        if isinstance(k, dict) and k.get("label") == "可同步的关系":
            k["n"] = str(n_reader)
            found = True
            break
    if not found and n_reader:
        kpis.insert(0, {"n": str(n_reader), "label": "可同步的关系"})
    data["kpis"] = kpis


def build_published_projection(draft: dict | None) -> dict:
    """draft_json → published_json 唯一投影入口。

    - 进草稿且完整的关系（strong/parallel/watch）全部进入 relations，按强度排序
    - 剥离内部 `_…` 字段
    - KPI「可同步的关系」对齐读者卡数量
    """
    src = draft if isinstance(draft, dict) else {}
    data = copy.deepcopy(src)
    for k in list(data.keys()):
        if k.startswith("_") or k in _INTERNAL_DRAFT_KEYS:
            data.pop(k, None)

    raw_rels = [r for r in (data.get("relations") or []) if isinstance(r, dict)]
    flagged = attach_reader_flags(raw_rels)
    reader, _backlog = split_relations_for_publish(flagged)
    # 双保险：可见性以 reader_visible 为准（完整卡），不因 tier 再砍
    reader = [r for r in reader if reader_visible(r)]
    data["relations"] = sort_relations_by_strength(reader)
    _sync_relation_kpi(data, len(data["relations"]))
    return data
