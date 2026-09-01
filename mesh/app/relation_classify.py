"""Relation blocker 分类：补 evidence / 降级 weak / 直接 block。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .aggregator import sanitize_owner_team
from .attribution_verify import narrative_team_from_label
from .owner_guard import (
    _DETAIL_TEAM,
    _item_matches_entity,
    _teams_in_relation,
    _title_entities,
    cross_team_provenance_ok,
    team_has_entity_items,
)

CAT_REAL_MISSING = "real_missing_evidence"
CAT_WEAK = "weak_speculative"
CAT_NARRATIVE = "narrative_only"
CAT_WRONG = "obviously_wrong"

CAT_LABELS = {
    CAT_REAL_MISSING: "真实关系，缺 owner evidence",
    CAT_WEAK: "弱关系 / 推测关系",
    CAT_NARRATIVE: "纯 narrative 推断",
    CAT_WRONG: "明显错误关系",
}

ACTION_ADD_EVIDENCE = "add_evidence"
ACTION_DOWNGRADE_WEAK = "downgrade_weak"
ACTION_BLOCK = "block"


@dataclass
class TeamGap:
    team: str
    has_entity_match: bool
    has_text_match: bool
    in_details: bool
    in_evidence: bool
    item_ids: list[int] = field(default_factory=list)


@dataclass
class RelationClassification:
    title: str
    category: str
    recommended_action: str
    missing_teams: list[str]
    supported_teams: list[str]
    rel_teams: list[str]
    weak: bool
    label: str
    provenance_ok: bool | None
    evidence_count: int
    reason: str
    team_gaps: list[TeamGap] = field(default_factory=list)


def _parse_draft(draft_json: str | dict | None) -> dict:
    if isinstance(draft_json, dict):
        return draft_json
    if not draft_json or not str(draft_json).strip():
        return {}
    try:
        return json.loads(draft_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _text_mentions_title(it: dict, title: str) -> bool:
    text = (it.get("text") or "").lower()
    if not text:
        return False
    parts = [p.lower() for p in _title_entities(title) if len(p) >= 2]
    return any(p in text for p in parts)


def _team_in_details(rel: dict, team: str) -> bool:
    for line in rel.get("details") or []:
        m = _DETAIL_TEAM.match(str(line).strip())
        if m and sanitize_owner_team(m.group(1).strip()) == team:
            return True
    return False


def _evidence_teams(rel: dict) -> set[str]:
    out: set[str] = set()
    for ev in rel.get("evidence") or []:
        t = sanitize_owner_team(ev.get("team"))
        if t:
            out.add(t)
    return out


def _active_items(items: list[dict]) -> list[dict]:
    return [x for x in items if not int(x.get("blocked") or 0) and not x.get("merged_into")]


def _items_for_team(items: list[dict], team: str) -> list[dict]:
    team = sanitize_owner_team(team) or ""
    return [it for it in items if sanitize_owner_team(it.get("owner_team")) == team]


def _narrative_evidence_count(rel: dict, active: list[dict]) -> int:
    n = 0
    for ev in rel.get("evidence") or []:
        row = next((x for x in active if x.get("id") == ev.get("item_id")), None)
        if not row:
            continue
        nar = narrative_team_from_label(ev.get("source_label") or row.get("source_label"))
        owner = sanitize_owner_team(row.get("owner_team"))
        if nar and owner and nar != owner:
            n += 1
    return n


def classify_relation(rel: dict, items: list[dict], *, candidate: dict | None = None) -> RelationClassification | None:
    title = (rel.get("title") or "").strip()
    if not title:
        return None

    active = _active_items(items)
    rel_teams = _teams_in_relation(rel)
    if len(rel_teams) < 2:
        return None

    supported = [t for t in rel_teams if team_has_entity_items(active, title, t)]
    missing = [t for t in rel_teams if t not in supported]
    if not missing:
        return None

    ev_teams = _evidence_teams(rel)
    gaps: list[TeamGap] = []
    for t in missing:
        team_items = _items_for_team(active, t)
        ids_entity = [it["id"] for it in team_items if _item_matches_entity(it, title)]
        ids_text = [it["id"] for it in team_items if _text_mentions_title(it, title) and it["id"] not in ids_entity]
        gaps.append(TeamGap(
            team=t,
            has_entity_match=bool(ids_entity),
            has_text_match=bool(ids_text),
            in_details=_team_in_details(rel, t),
            in_evidence=t in ev_teams,
            item_ids=ids_entity + ids_text,
        ))

    weak = bool(rel.get("weak"))
    label = (rel.get("label") or "").strip()
    provenance_ok = rel.get("provenance_ok")
    if provenance_ok is None and candidate:
        provenance_ok = candidate.get("provenance_ok")
    evidence_count = len(rel.get("evidence") or [])

    any_text_not_entity = any(g.has_text_match and not g.has_entity_match for g in gaps)
    all_missing_narrative_only = all(
        g.in_details and not g.in_evidence and not g.has_entity_match and not g.has_text_match
        for g in gaps
    )
    any_missing_only_details = any(
        g.in_details and not g.in_evidence and not g.has_text_match for g in gaps
    )
    has_supported = bool(supported)
    corpus_hits = sum(
        1 for it in active
        if _item_matches_entity(it, title) or _text_mentions_title(it, title)
    )

    if corpus_hits == 0:
        category, action, reason = CAT_WRONG, ACTION_BLOCK, "标题实体在 item 语料中无任何命中"
    elif provenance_ok is False or not cross_team_provenance_ok(active, title, rel_teams):
        category, action, reason = CAT_WRONG, ACTION_BLOCK, "跨团队 provenance 不成立"
    elif any_text_not_entity or (candidate and candidate.get("provenance_ok")):
        category, action, reason = CAT_REAL_MISSING, ACTION_ADD_EVIDENCE, "有相关 item/text，但 entities 未对齐标题"
    elif weak or "一方接触" in label:
        category, action, reason = CAT_WEAK, ACTION_DOWNGRADE_WEAK, "已标 weak 或「一方接触」型"
    elif all_missing_narrative_only or (any_missing_only_details and has_supported):
        category, action, reason = CAT_NARRATIVE, ACTION_DOWNGRADE_WEAK, "缺失团队仅来自 details 叙事"
    elif not has_supported and evidence_count == 0:
        category, action, reason = CAT_WRONG, ACTION_BLOCK, "无 entity 匹配且无 evidence"
    elif not has_supported:
        category, action, reason = CAT_NARRATIVE, ACTION_DOWNGRADE_WEAK, "details 推断多团队，无结构化 entity 命中"
    else:
        category, action, reason = CAT_WEAK, ACTION_DOWNGRADE_WEAK, "部分团队缺 entity 对齐"

    return RelationClassification(
        title=title,
        category=category,
        recommended_action=action,
        missing_teams=missing,
        supported_teams=supported,
        rel_teams=rel_teams,
        weak=weak,
        label=label,
        provenance_ok=provenance_ok if provenance_ok is not None else None,
        evidence_count=evidence_count,
        reason=reason,
        team_gaps=gaps,
    )


def classify_blocked_relations(
    draft_json: str | dict | None,
    items: list[dict],
    *,
    candidates: list[dict] | None = None,
) -> list[RelationClassification]:
    draft = _parse_draft(draft_json)
    cand_by_title = {(c.get("title") or "").strip().lower(): c for c in (candidates or []) if c.get("title")}
    out: list[RelationClassification] = []
    seen: set[str] = set()
    for rel in draft.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        title = (rel.get("title") or "").strip()
        if not title or title in seen:
            continue
        cls = classify_relation(rel, items, candidate=cand_by_title.get(title.lower()))
        if cls:
            seen.add(title)
            out.append(cls)
    return out


def audit_all_relations(
    draft_json: str | dict | None,
    items: list[dict],
    *,
    candidates: list[dict] | None = None,
) -> list[RelationClassification]:
    """含 blocker 关系 + 叙事/弱关系风险（全量审计）。"""
    out = classify_blocked_relations(draft_json, items, candidates=candidates)
    blocked_titles = {r.title for r in out}
    draft = _parse_draft(draft_json)
    active = _active_items(items)

    for rel in draft.get("relations") or []:
        if not isinstance(rel, dict):
            continue
        title = (rel.get("title") or "").strip()
        if not title or title in blocked_titles:
            continue
        rel_teams = _teams_in_relation(rel)
        if len(rel_teams) < 2:
            continue
        supported = [t for t in rel_teams if team_has_entity_items(active, title, t)]
        nar_ev = _narrative_evidence_count(rel, active)
        if rel.get("weak") or "一方接触" in (rel.get("label") or ""):
            out.append(RelationClassification(
                title=title,
                category=CAT_WEAK,
                recommended_action=ACTION_DOWNGRADE_WEAK,
                missing_teams=[t for t in rel_teams if t not in supported],
                supported_teams=supported,
                rel_teams=rel_teams,
                weak=bool(rel.get("weak")),
                label=(rel.get("label") or "").strip(),
                provenance_ok=rel.get("provenance_ok"),
                evidence_count=len(rel.get("evidence") or []),
                reason="weak 或一方接触标签",
            ))
        elif nar_ev:
            out.append(RelationClassification(
                title=title,
                category=CAT_NARRATIVE,
                recommended_action=ACTION_DOWNGRADE_WEAK,
                missing_teams=[],
                supported_teams=supported,
                rel_teams=rel_teams,
                weak=False,
                label=(rel.get("label") or "").strip(),
                provenance_ok=rel.get("provenance_ok"),
                evidence_count=len(rel.get("evidence") or []),
                reason=f"{nar_ev} 条 evidence source_label 叙事 ≠ owner_team",
            ))
    return out


def summarize_classifications(rows: list[RelationClassification]) -> dict:
    by_cat: dict[str, list[str]] = {}
    by_action: dict[str, list[str]] = {}
    for r in rows:
        by_cat.setdefault(r.category, []).append(r.title)
        by_action.setdefault(r.recommended_action, []).append(r.title)
    return {
        "total": len(rows),
        "by_category": {CAT_LABELS.get(k, k): v for k, v in by_cat.items()},
        "by_action": by_action,
    }
