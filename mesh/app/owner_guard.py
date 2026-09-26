"""条目归属与跨团队关系的硬约束（T13 / 聚合包）。"""
from __future__ import annotations

import json
import re
from collections import Counter

from . import ingest
from .aggregator import sanitize_owner_team, INVALID_OWNER_TEAMS

_SECTION_HEADER = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9 /·&]{2,28})\s*[·•]\s*.+$"
)

# details 里「X 记录：」前缀 → 团队名
_DETAIL_TEAM = re.compile(
    r"^([\u4e00-\u9fffA-Za-z0-9 /·&]{2,24})\s*记录[：:]"
)


def parse_section_team(line: str) -> str | None:
    """「编辑部 · 沟通记录」→ 编辑部。"""
    s = (line or "").strip()
    if not s or len(s) > 80:
        return None
    m = _SECTION_HEADER.match(s)
    if not m:
        return None
    return sanitize_owner_team(m.group(1).strip())


def owner_hint_for_lines(lines: list[str], *, fallback: str = "") -> str:
    """段内最后一个「团队 · 小节」标题作为归属提示。"""
    hint = ""
    for line in lines:
        t = parse_section_team(line)
        if t:
            hint = t
    return hint or sanitize_owner_team(fallback) or ""


def resolve_item_owner(
    it: dict,
    *,
    source_team: str,
    segment_team: str | None = None,
) -> str | None:
    """入库前解析 owner_team（见 app.attribution.resolve_attribution）。"""
    from .attribution import resolve_item_owner as _resolve

    return _resolve(it, source_team=source_team, segment_team=segment_team)


def normalize_pointer(pointer: str | None) -> str:
    from .segment_cache import strip_segment_digest_prefix

    bare = strip_segment_digest_prefix(pointer)
    return re.sub(r"\s+", " ", (bare or "").strip())


def provenance_key(it: dict) -> str:
    """同源同 pointer 视为同一出处（含 source_id，避免跨文件空 pointer 碰撞）。"""
    sid = it.get("source_id")
    sid_part = f"s{sid}:" if sid is not None and str(sid).strip() != "" else ""
    ptr = normalize_pointer(it.get("pointer"))
    if ptr:
        return f"{sid_part}{ptr.lower()}"
    ents = it.get("entities") or []
    if isinstance(ents, str):
        try:
            ents = json.loads(ents)
        except (json.JSONDecodeError, TypeError):
            ents = []
    names = sorted({str(x).strip() for x in ents if str(x).strip()})
    if names:
        return f"{sid_part}{'|'.join(names[:3]).lower()}"
    text = re.sub(r"\s+", " ", (it.get("text") or "")[:80]).strip().lower()
    return f"{sid_part}{text}" if text else f"{sid_part}__empty__"


def apply_item_owner_guards(items: list[dict]) -> list[dict]:
    """同一出处只允许一个 owner_team；冲突项标 blocked。"""
    groups: dict[str, list[dict]] = {}
    for it in items:
        key = provenance_key(it)
        groups.setdefault(key, []).append(it)

    out: list[dict] = []
    for key, group in groups.items():
        if len(group) == 1:
            out.append(group[0])
            continue
        owners = [sanitize_owner_team(x.get("owner_team")) or sanitize_owner_team(x.get("_segment_team")) for x in group]
        hints = [sanitize_owner_team(x.get("_segment_team")) for x in group]
        canonical = _pick_canonical_owner(owners, hints)
        for it in group:
            row = dict(it)
            cur = sanitize_owner_team(row.get("owner_team")) or sanitize_owner_team(row.get("_segment_team"))
            if canonical and cur and cur != canonical:
                row["blocked"] = 1
                row["_owner_conflict"] = canonical
            out.append(row)
    return out


def _pick_canonical_owner(owners: list[str | None], hints: list[str | None]) -> str | None:
    hint = next((h for h in reversed(hints) if h), None)
    if hint:
        return hint
    counted = Counter(o for o in owners if o)
    if not counted:
        return None
    return counted.most_common(1)[0][0]


def _entity_names(it: dict) -> set[str]:
    ents = it.get("entities") or []
    if isinstance(ents, str):
        try:
            ents = json.loads(ents)
        except (json.JSONDecodeError, TypeError):
            ents = []
    return {str(x).strip() for x in ents if str(x).strip()}


def is_suggested_team_badge(t: str) -> bool:
    s = str(t or "").strip()
    return s.startswith("→") or s.startswith("->")


def suggested_team_badges(r: dict) -> list[str]:
    return [str(t).strip() for t in r.get("teams") or [] if is_suggested_team_badge(str(t))]


def solid_team_badges(r: dict) -> list[str]:
    return [str(t).strip() for t in r.get("teams") or [] if str(t).strip() and not is_suggested_team_badge(str(t))]


def normalize_relation_team_badges(rel: dict, *, suggest_extra_solid: bool = False) -> dict:
    """
    实线徽章 = 有 evidence / 记录的团队；虚线徽章 = 「→ 团队」（建议相关团队关注）。
    虚线只在团队标签上，不在整张关系卡上。
    """
    rel = dict(rel)
    evidence_teams: set[str] = set()
    for e in rel.get("evidence") or []:
        if isinstance(e, dict) and e.get("team"):
            ot = sanitize_owner_team(e["team"])
            if ot:
                evidence_teams.add(ot)
    for line in rel.get("details") or []:
        m = _DETAIL_TEAM.match(str(line).strip())
        if m:
            ot = sanitize_owner_team(m.group(1).strip())
            if ot:
                evidence_teams.add(ot)

    solid_names: list[str] = []
    for t in rel.get("teams") or []:
        raw = str(t).strip()
        if raw and not is_suggested_team_badge(raw):
            name = sanitize_owner_team(raw) or raw
            if name and name not in solid_names:
                solid_names.append(name)

    if not evidence_teams and suggest_extra_solid and len(solid_names) >= 2:
        evidence_teams = {solid_names[0]}

    out: list[str] = []
    seen_solid: set[str] = set()
    seen_sug: set[str] = set()
    for t in rel.get("teams") or []:
        raw = str(t).strip()
        if not raw:
            continue
        if is_suggested_team_badge(raw):
            name = sanitize_owner_team(raw.lstrip("→").lstrip("->").strip()) or raw.lstrip("→").lstrip("->").strip()
            # 已有实线/证据的同队不再挂虚线
            if name and (name in seen_solid or name in evidence_teams):
                continue
            sug = f"→ {name}" if name else raw
            if sug not in seen_sug:
                out.append(sug)
                seen_sug.add(sug)
            continue
        name = sanitize_owner_team(raw) or raw
        if evidence_teams and name not in evidence_teams:
            sug = f"→ {name}"
            if name in seen_solid or sug in seen_sug:
                continue
            if sug not in seen_sug:
                out.append(sug)
                seen_sug.add(sug)
        elif name not in seen_solid:
            out.append(name)
            seen_solid.add(name)
            # 实线出现后清掉同名虚线（列表里 → 可能更早）
            sug = f"→ {name}"
            if sug in seen_sug:
                out = [x for x in out if x != sug]
                seen_sug.discard(sug)
    rel["teams"] = out
    rel["weak"] = bool(seen_sug)
    return rel


def _teams_in_relation(r: dict) -> list[str]:
    teams: list[str] = []
    for t in r.get("teams") or []:
        s = str(t).strip().lstrip("→").lstrip("->").strip()
        ot = sanitize_owner_team(s)
        if ot and ot not in teams:
            teams.append(ot)
    for line in r.get("details") or []:
        m = _DETAIL_TEAM.match(str(line).strip())
        if m:
            ot = sanitize_owner_team(m.group(1).strip())
            if ot and ot not in teams:
                teams.append(ot)
    return teams


def _title_entities(title: str) -> set[str]:
    """从卡标题抽出可匹配实体名（兼容「OJO：…」「张岩（Notta）…」）。"""
    s = (title or "").strip()
    if not s:
        return set()
    parts = re.split(r"[·•/\s：:，,、；;\|（）()【】\[\]「」]+", s)
    out = {p.strip() for p in parts if len(p.strip()) >= 2}
    # 标题主体常在冒号前：OJO：编辑部已发稿…
    head = re.split(r"[：:]", s, maxsplit=1)[0].strip()
    if len(head) >= 2:
        out.add(head)
        # 人（公司）形态：戴若犁（Noitom）
        m = re.match(r"^(.{2,24}?)\s*[（(]([^）)]{2,40})[）)]", head)
        if m:
            out.add(m.group(1).strip())
            out.add(m.group(2).strip())
    return out


def _item_matches_entity(it: dict, title: str) -> bool:
    names = {n.lower() for n in _entity_names(it)}
    if not names:
        return False
    for part in _title_entities(title):
        if part.lower() in names:
            return True
    if title.strip().lower() in names:
        return True
    return bool(names & {x.lower() for x in _title_entities(title)})


def _provenance_buckets(items: list[dict], title: str) -> dict[tuple[str, str], set[str]]:
    """title 主体 → {(source_id, pointer_key) → owners}."""
    buckets: dict[tuple[str, str], set[str]] = {}
    for it in items:
        if it.get("blocked"):
            continue
        if not _item_matches_entity(it, title):
            continue
        sid = str(it.get("source_id") or it.get("_source_id") or "")
        pk = provenance_key(it)
        buckets.setdefault((sid, pk), set())
        ot = sanitize_owner_team(it.get("owner_team"))
        if ot:
            buckets[(sid, pk)].add(ot)
    return buckets


def team_has_entity_items(items: list[dict], title: str, team: str) -> bool:
    team = sanitize_owner_team(team) or ""
    for it in items:
        if it.get("blocked"):
            continue
        if sanitize_owner_team(it.get("owner_team")) != team:
            continue
        if _item_matches_entity(it, title):
            return True
    return False


def _relation_entity_title(r: dict) -> str:
    """两阶段 gate 卡用 candidate_title 做实体匹配，避免 narrative 标题误裁 teams。"""
    return ((r.get("candidate_title") or r.get("title") or "").strip())


def relation_team_supported(items: list[dict], rel: dict, team: str) -> bool:
    """团队是否被本卡论证支撑。

    优先：evidence 引用的 item.owner_team == team（论点/证据对齐）。
    其次：candidate_title / title 与该团队条目实体重合（兼容旧卡）。
    """
    team = sanitize_owner_team(team) or ""
    if not team or not isinstance(rel, dict):
        return False
    by_id: dict = {}
    for it in items:
        iid = it.get("id")
        if iid is not None:
            by_id[iid] = it
    for ev in rel.get("evidence") or []:
        if not isinstance(ev, dict):
            continue
        iid = ev.get("item_id")
        row = by_id.get(iid) if iid is not None else None
        if not row or row.get("blocked") or row.get("merged_into"):
            continue
        if sanitize_owner_team(row.get("owner_team")) == team:
            return True
    title = _relation_entity_title(rel)
    if title and team_has_entity_items(items, title, team):
        return True
    return False


def cross_team_provenance_ok(items: list[dict], title: str, teams: list[str]) -> bool:
    """两团队各有一手：必须来自不同 (source_id, pointer) 桶。"""
    if len(teams) < 2:
        return True
    buckets = _provenance_buckets(items, title)
    if not buckets:
        return True
    team_buckets: dict[str, set[tuple[str, str]]] = {t: set() for t in teams}
    for key, owners in buckets.items():
        for t in teams:
            if t in owners:
                team_buckets[t].add(key)
    present = [t for t in teams if team_buckets[t]]
    if len(present) < 2:
        return True
    # 若两团队只出现在同一 (source_id, pointer) 桶 → 假跨团队
    for i, a in enumerate(present):
        for b in present[i + 1 :]:
            shared = team_buckets[a] & team_buckets[b]
            if shared and team_buckets[a] == shared and team_buckets[b] == shared:
                return False
    return True


def _gate_locked_relation(r: dict) -> bool:
    return bool(r.get("candidate_id") and r.get("provenance_ok") and (r.get("evidence") or []))


def filter_draft_relations(draft: dict, items: list[dict]) -> dict:
    """去掉同源同 pointer 误标的跨团队关系/detail。"""
    data = dict(draft)
    rels = []
    for r in data.get("relations") or []:
        rel = _sanitize_relation(r, items)
        if rel:
            teams = _teams_in_relation(rel)
            entity_title = _relation_entity_title(rel)
            if entity_title:
                if not teams:
                    continue
                if not _gate_locked_relation(rel) and not any(
                    team_has_entity_items(items, entity_title, t) for t in teams
                ):
                    continue
            _align_sources(rel, teams)
            rels.append(rel)
    data["relations"] = [x for x in rels if x]
    return data


def _sanitize_relation(r: dict, items: list[dict]) -> dict | None:
    if _gate_locked_relation(r):
        rel = dict(r)
        teams = _teams_in_relation(rel)
        _align_sources(rel, teams)
        return rel
    title = _relation_entity_title(r)
    details_in = list(r.get("details") or [])
    details: list[str] = []
    for line in details_in:
        m = _DETAIL_TEAM.match(str(line).strip())
        if m:
            ot = sanitize_owner_team(m.group(1).strip())
            if ot and title and not team_has_entity_items(items, title, ot):
                continue
        details.append(line)
    teams = _teams_in_relation({"details": details, "teams": r.get("teams") or []})
    teams = [t for t in teams if not title or team_has_entity_items(items, title, t)]
    if len(teams) >= 2 and title and not cross_team_provenance_ok(items, title, teams):
        teams_ok = [t for t in teams if team_has_entity_items(items, title, t)]
        if len(teams_ok) < 2:
            if len(teams_ok) == 1 and details:
                nr = dict(r)
                kept_details: list[str] = []
                for d in details:
                    m = _DETAIL_TEAM.match(str(d).strip())
                    if m:
                        ot = sanitize_owner_team(m.group(1).strip())
                        if ot and ot not in teams_ok:
                            continue
                    kept_details.append(d)
                nr["details"] = kept_details
                nr["teams"] = teams_ok
                nr["weak"] = True
                nr["body"] = f"{teams_ok[0]}有本期记录（未找到其他团队的独立出处）。"
                _align_sources(nr, teams_ok)
                return nr
            return None
        nr = dict(r)
        nr["details"] = details
        nr["teams"] = teams_ok
        nr["weak"] = True
        _align_sources(nr, teams_ok)
        return nr
    if details != details_in or teams != _teams_in_relation(r):
        nr = dict(r)
        nr["details"] = details
        nr["teams"] = teams
        if len(teams) == 1 and details_in != details:
            nr["weak"] = True
            if "各有一手" in (nr.get("body") or ""):
                nr["body"] = f"{teams[0]}有本期记录（未找到其他团队的独立出处）。"
        _align_sources(nr, teams)
        return nr
    return r


def _align_sources(rel: dict, teams: list[str]) -> None:
    markers = _foreign_team_markers(teams)
    srcs = [s for s in (rel.get("sources") or []) if not any(m in str(s) for m in markers)]
    if srcs:
        rel["sources"] = srcs


def _foreign_team_markers(allowed: list[str]) -> list[str]:
    from . import ingest

    allow = set(allowed)
    out = []
    for t in ingest.TEAMS:
        if t in ("内容中心·数据聚合", "其他", "外部媒体"):
            continue
        if t not in allow:
            out.append(t)
    return out
