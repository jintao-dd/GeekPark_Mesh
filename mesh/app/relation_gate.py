"""上线前关系证据 / 叙事核对闸门。"""
from __future__ import annotations

import json
from typing import Any

from .issue_verify import collect_unsupported_flags
from .owner_guard import (
    cross_team_provenance_ok,
    solid_team_badges,
    suggested_team_badges,
    team_has_entity_items,
)
from .aggregator import sanitize_owner_team


def _parse_draft(draft_json: str | dict | None) -> dict:
    if isinstance(draft_json, dict):
        return draft_json
    if not draft_json or not str(draft_json).strip():
        return {}
    try:
        return json.loads(draft_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _solid_teams(r: dict) -> list[str]:
    out: list[str] = []
    for t in solid_team_badges(r):
        ot = sanitize_owner_team(t) or str(t).strip()
        if ot and ot not in out:
            out.append(ot)
    return out


def relation_publish_blockers(
    draft_json: str | dict | None,
    items: list[dict],
) -> list[str]:
    """
    发布前关系闸门：
    - 已有 evidence 的 watch/weak 卡可上线（读者页展示全部 grounded）
    - 「→ 团队」虚线不要求该团队有 evidence
    - 双实线团队均须有 evidence / provenance
    - 无 evidence 且非虚线观察卡 → 拦截
    """
    draft = _parse_draft(draft_json)
    rels = draft.get("relations") or []
    if not rels:
        return []

    errs: list[str] = []

    for r in rels:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        label = (r.get("label") or "").strip()
        solid = _solid_teams(r)
        suggested = suggested_team_badges(r)
        has_ev = bool(r.get("evidence") or [])

        # 虚线观察 / 一方接触：有 evidence 即可上线
        if suggested and len(solid) < 2 and has_ev:
            continue
        if (r.get("weak") or r.get("decision_tier") == "watch") and has_ev:
            continue

        if len(solid) >= 2 and title:
            missing = [t for t in solid if not team_has_entity_items(items, title, t)]
            if missing:
                errs.append(f"关系「{title}」缺少团队证据：{'、'.join(missing)}")
            elif not cross_team_provenance_ok(items, title, solid):
                errs.append(
                    f"关系「{title}」跨团队出处不足（同源同 pointer 不能冒充两团队各有一手）"
                )

        details = r.get("details") or []
        sources = r.get("sources") or []
        if not details and not sources and label and not r.get("weak"):
            errs.append(f"关系「{title}」缺少 details/sources，不能作为强关系上线")

        if not has_ev and not suggested:
            errs.append(f"关系「{title}」缺少 evidence[]，不能作为强关系上线")

    return errs


def issue_publish_blockers(draft_json: str | dict | None, items: list[dict]) -> list[str]:
    """关系闸门 + Verify v2 硬拦截（trim 计数仅信息，不拦上线）。"""
    draft = _parse_draft(draft_json)
    errs = relation_publish_blockers(draft, items)
    for flag in collect_unsupported_flags(draft, hard_only=True):
        if flag not in errs:
            errs.append(flag)
    return errs


def relation_fails_grounding(rel: dict, items: list[dict]) -> list[str]:
    """单张关系卡是否经不起论证（缺团队证据 / body 无法证明等）。"""
    if not isinstance(rel, dict):
        return ["非对象关系"]
    mini = {"relations": [rel]}
    errs = relation_publish_blockers(mini, items)
    for flag in collect_unsupported_flags(mini, hard_only=True):
        if flag not in errs:
            errs.append(flag)
    try:
        from .attribution_verify import scan_draft

        for b in scan_draft(mini, items).blockers:
            if b not in errs:
                errs.append(b)
    except Exception:
        pass
    return errs


def filter_ungrounded_relations(
    draft_json: str | dict | None,
    items: list[dict],
) -> tuple[dict, list[str]]:
    """不过关的关系卡直接拿掉，其余保留。返回 (新草稿, 被拿掉的标题)。

    产品约定：论证失败 = 不展示该卡，不是整期进不了预览。
    """
    draft = dict(_parse_draft(draft_json))
    kept: list[dict] = []
    dropped: list[str] = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        if relation_fails_grounding(r, items):
            dropped.append(title)
        else:
            kept.append(r)
    draft["relations"] = kept
    if dropped:
        draft["_relations_dropped_ungrounded"] = dropped
    else:
        draft.pop("_relations_dropped_ungrounded", None)
    return draft, dropped


def items_for_issue(con, issue_id: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT id, source_id, owner_team, pointer, entities, blocked, text
           FROM items WHERE issue_id=?""",
        (issue_id,),
    ).fetchall()
    return [dict(r) for r in rows]
