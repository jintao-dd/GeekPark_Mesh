"""Publish 前 Attribution / Narrative 分离校验（不改 Attribution 语义）。"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from . import ingest
from .aggregator import AGG_TEAM, INVALID_OWNER_TEAMS, sanitize_owner_team
from .attribution import (
    PUBLISHABLE_PROVENANCES,
    PROVENANCE_LLM_HINT,
    PROVENANCE_MANUAL,
    PROVENANCE_SEGMENT,
    PROVENANCE_UNKNOWN,
    manual_owner_from_source,
)
from .owner_guard import (
    _DETAIL_TEAM,
    relation_team_supported,
    solid_team_badges,
    suggested_team_badges,
)

_NARRATIVE_PREFIX = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9 /·&]{2,24})(?:沟通|建联|记录|例会|周报|数据|妙记)"
)


def _formal_teams() -> set[str]:
    return {t for t in ingest.TEAMS if t not in INVALID_OWNER_TEAMS}


def narrative_team_from_label(label: str) -> str | None:
    """source_label 叙事里暗示的团队（仅展示层，不参与 owner 判定）。"""
    s = (label or "").strip()
    if not s:
        return None
    # 来源类型描述（非 owner 宣称），见 docs/NARRATIVE.md
    if s.startswith("外部媒体"):
        return None
    for team in sorted(_formal_teams(), key=len, reverse=True):
        if s.startswith(team):
            return team
    m = _NARRATIVE_PREFIX.match(s)
    if m:
        return sanitize_owner_team(m.group(1).strip())
    m = _DETAIL_TEAM.match(s)
    if m:
        return sanitize_owner_team(m.group(1).strip())
    return None


@dataclass
class AttributionScan:
    blockers: list[str] = field(default_factory=list)
    narrative_flags: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.blockers


def _parse_draft(draft_json: str | dict | None) -> dict:
    if isinstance(draft_json, dict):
        return draft_json
    if not draft_json or not str(draft_json).strip():
        return {}
    try:
        return json.loads(draft_json)
    except (json.JSONDecodeError, TypeError):
        return {}


def _load_items(con, issue_id: int) -> list[dict]:
    rows = con.execute(
        """SELECT i.id, i.source_id, i.owner_team, i.owner_provenance, i.llm_owner_team_hint,
                  i.blocked, i.source_label, i.pointer, i.merged_into, i.entities, i.text,
                  s.team AS source_team
           FROM items i
           LEFT JOIN sources s ON s.id = i.source_id
           WHERE i.issue_id=?""",
        (issue_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def scan_items(items: list[dict]) -> AttributionScan:
    """Attribution 硬约束（1–5, 8–9 的 item 层）。"""
    out = AttributionScan()
    formal = _formal_teams()
    n_pub = 0
    n_narrative_mismatch = 0

    for it in items:
        if it.get("merged_into"):
            continue
        iid = it.get("id")
        owner = sanitize_owner_team(it.get("owner_team"))
        prov = (it.get("owner_provenance") or "").strip()
        blocked = int(it.get("blocked") or 0)
        src_team = (it.get("source_team") or "").strip()
        hint = sanitize_owner_team(it.get("llm_owner_team_hint"))
        label = (it.get("source_label") or "").strip()

        if blocked:
            continue
        n_pub += 1

        # 1. owner ∈ 正式团队
        if not owner or owner not in formal:
            out.blockers.append(f"item #{iid} owner_team 不在正式团队集合：{owner!r}")

        # 2. provenance 可发布
        if prov not in PUBLISHABLE_PROVENANCES:
            out.blockers.append(
                f"item #{iid} owner_provenance={prov!r} 不可发布（须 manual/segment_rule/source）"
            )
        elif not prov:
            out.blockers.append(f"item #{iid} 缺少 owner_provenance")

        # 3. manual → owner 必须等于人工选择
        if prov == PROVENANCE_MANUAL:
            expected = manual_owner_from_source(src_team)
            if expected and owner != expected:
                out.blockers.append(
                    f"item #{iid} manual 归属不一致：owner={owner!r} 期望={expected!r}（source.team={src_team!r}）"
                )

        # 4. 聚合占位不得作 item owner
        if owner in INVALID_OWNER_TEAMS or owner == AGG_TEAM:
            out.blockers.append(f"item #{iid} 占位团队不得作 owner_team：{owner!r}")
        if src_team in (AGG_TEAM,) + tuple(INVALID_OWNER_TEAMS) and owner == sanitize_owner_team(
            ingest.owner_team_for_pick(src_team)
        ):
            pick_owner = sanitize_owner_team(ingest.owner_team_for_pick(src_team))
            if pick_owner in INVALID_OWNER_TEAMS or not pick_owner:
                if owner and prov not in (PROVENANCE_SEGMENT, PROVENANCE_MANUAL):
                    out.blockers.append(
                        f"item #{iid} 来源为聚合占位但 owner 非 segment/manual：{owner!r}/{prov!r}"
                    )

        # 5. LLM hint 不得反向覆盖正式 owner
        if prov in (PROVENANCE_LLM_HINT, PROVENANCE_UNKNOWN) and owner:
            out.blockers.append(f"item #{iid} LLM/unknown provenance 却写了 owner_team={owner!r}")
        if hint and owner and prov in PUBLISHABLE_PROVENANCES and hint != owner:
            pass  # 允许 hint 与 owner 不同

        # 9 + narrative 分离：source_label 叙事团队 ≠ owner（仅 flag，不 block）
        nar = narrative_team_from_label(label)
        if nar and owner and nar != owner:
            n_narrative_mismatch += 1
            if len(out.narrative_flags) < 20:
                out.narrative_flags.append(
                    f"item #{iid} narrative/source_label 暗示 {nar!r}，owner_team={owner!r}（pointer={it.get('pointer')!r}）"
                )

    out.stats["publishable_items"] = n_pub
    out.stats["narrative_label_mismatch"] = n_narrative_mismatch
    return out


def scan_draft(draft_json: str | dict | None, items: list[dict]) -> AttributionScan:
    """Evidence / relation 硬约束（6–8, 7）。"""
    out = AttributionScan()
    draft = _parse_draft(draft_json)
    by_id = {x["id"]: x for x in items if x.get("id") is not None}
    blocked_ids = {x["id"] for x in items if int(x.get("blocked") or 0)}

    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip() or "（无标题）"
        # 只校验实线团队；「→ 投资团队」等虚线路由不要求有 item.owner_team
        rel_teams = []
        for t in solid_team_badges(r):
            ot = sanitize_owner_team(t) or str(t).strip()
            if ot and ot not in rel_teams:
                rel_teams.append(ot)

        for ev in r.get("evidence") or []:
            iid = ev.get("item_id")
            if iid is None:
                continue
            if iid in blocked_ids:
                out.blockers.append(f"关系「{title}」evidence 引用 blocked item #{iid}")
                continue
            row = by_id.get(iid)
            if not row:
                out.blockers.append(f"关系「{title}」evidence item #{iid} 不存在")
                continue
            item_owner = sanitize_owner_team(row.get("owner_team"))
            ev_team = sanitize_owner_team(ev.get("team"))
            # 6. evidence.team == item.owner_team
            if item_owner and ev_team and ev_team != item_owner:
                out.blockers.append(
                    f"关系「{title}」evidence #{iid} team={ev_team!r} ≠ item.owner_team={item_owner!r}"
                )
            elif item_owner and not ev_team:
                out.blockers.append(f"关系「{title}」evidence #{iid} 缺少 team")

            # narrative in evidence must not override
            nar = narrative_team_from_label(ev.get("source_label") or row.get("source_label"))
            if nar and item_owner and nar != item_owner:
                if len(out.narrative_flags) < 30:
                    out.narrative_flags.append(
                        f"关系「{title}」evidence #{iid} source_label 叙事 {nar!r} ≠ owner {item_owner!r}"
                    )

        # 7. 实线团队须有 item owner 支撑；虚线/一方观察卡不拦
        active = [
            x for x in items
            if not int(x.get("blocked") or 0) and not x.get("merged_into")
        ]
        if suggested_team_badges(r) and len(rel_teams) < 2:
            pass  # watch / one-sided：允许只有一侧实线
        elif len(rel_teams) >= 2 and title:
            for t in rel_teams:
                if not relation_team_supported(active, r, t):
                    out.blockers.append(
                        f"关系「{title}」团队 {t!r} 无 item.owner_team 支撑（非 narrative 推断）"
                    )

    out.stats["relations"] = len(draft.get("relations") or [])
    return out


def scan_issue(con, issue_id: int, draft_json: str | dict | None) -> AttributionScan:
    """Attribution + Narrative 全量扫描。"""
    items = _load_items(con, issue_id)
    item_scan = scan_items(items)
    draft_scan = scan_draft(draft_json, items)
    merged = AttributionScan(
        blockers=item_scan.blockers + draft_scan.blockers,
        narrative_flags=item_scan.narrative_flags + draft_scan.narrative_flags,
        stats={**item_scan.stats, **draft_scan.stats},
    )
    merged.stats["blockers"] = len(merged.blockers)
    merged.stats["narrative_flags"] = len(merged.narrative_flags)
    return merged


def attribution_publish_blockers(con, issue_id: int, draft_json: str | dict | None) -> list[str]:
    """供 publish_blockers 调用的 Attribution 硬拦截项。"""
    return scan_issue(con, issue_id, draft_json).blockers
