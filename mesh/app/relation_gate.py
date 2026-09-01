"""上线前关系证据 / 叙事核对闸门。"""
from __future__ import annotations

import json
from typing import Any

from .issue_verify import collect_unsupported_flags
from .owner_guard import (
    _teams_in_relation,
    cross_team_provenance_ok,
    solid_team_badges,
    suggested_team_badges,
    team_has_entity_items,
)


def _parse_draft(draft_json: str | dict | None) -> dict:
    if isinstance(draft_json, dict):
        return draft_json
    if not draft_json or not str(draft_json).strip():
        return {}
    try:
        return json.loads(draft_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def relation_publish_blockers(
    draft_json: str | dict | None,
    items: list[dict],
) -> list[str]:
    """
    发布前关系闸门：
    - needs_review 须 owner 核对后才能发 EDM/写 version
    - 「→ 团队」虚线建议关注，不要求该团队有 evidence
    - 实线团队均须有 evidence / provenance
    """
    draft = _parse_draft(draft_json)
    rels = draft.get("relations") or []
    if not rels:
        return []

    errs: list[str] = []
    review_titles: list[str] = []

    for r in rels:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        label = (r.get("label") or "").strip()
        solid = solid_team_badges(r)
        suggested = suggested_team_badges(r)

        if r.get("needs_review"):
            review_titles.append(title)
            continue

        if suggested and len(solid) < 2:
            continue

        teams = _teams_in_relation(r)
        if len(teams) >= 2 and title:
            missing = [t for t in teams if not team_has_entity_items(items, title, t)]
            if missing:
                errs.append(f"关系「{title}」缺少团队证据：{'、'.join(missing)}")
            elif not cross_team_provenance_ok(items, title, teams):
                errs.append(
                    f"关系「{title}」跨团队出处不足（同源同 pointer 不能冒充两团队各有一手）"
                )

        details = r.get("details") or []
        sources = r.get("sources") or []
        if not details and not sources and label:
            errs.append(f"关系「{title}」缺少 details/sources，不能作为强关系上线")

    if review_titles:
        preview = "、".join(review_titles[:3])
        more = f" 等 {len(review_titles)} 条" if len(review_titles) > 3 else ""
        errs.append(f"还有关系叙事待核对：{preview}{more}（请在控制台确认或删改）")

    for r in rels:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        if r.get("needs_review"):
            continue
        if suggested_team_badges(r) and not (r.get("evidence") or []):
            continue
        if not (r.get("evidence") or []):
            errs.append(f"关系「{title}」缺少 evidence[]，不能作为强关系上线")

    return errs


def issue_publish_blockers(draft_json: str | dict | None, items: list[dict]) -> list[str]:
    """关系闸门 + Verify v2 unsupported 汇总（Owner 发布前必过）。"""
    draft = _parse_draft(draft_json)
    errs = relation_publish_blockers(draft, items)
    for flag in collect_unsupported_flags(draft):
        if flag not in errs:
            errs.append(flag)
    return errs


def items_for_issue(con, issue_id: int) -> list[dict[str, Any]]:
    rows = con.execute(
        """SELECT id, source_id, owner_team, pointer, entities, blocked, text
           FROM items WHERE issue_id=?""",
        (issue_id,),
    ).fetchall()
    return [dict(r) for r in rows]
