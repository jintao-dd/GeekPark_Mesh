"""代码侧跨团队关系候选（draft 第一阶段）。"""
from __future__ import annotations

import json
from collections import defaultdict

from .aggregator import sanitize_owner_team
from .owner_guard import (
    cross_team_provenance_ok,
    filter_draft_relations,
    normalize_relation_team_badges,
    normalize_pointer,
    _title_entities,
    _DETAIL_TEAM,
)
from .relation_verify import editorial_weak

_MIN_ENTITY_LEN = 2
_MAX_SNIPPETS = 3
_SNIP_LEN = 120


def _parse_entities(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _item_row(it: dict) -> dict:
    return {
        "id": it.get("id"),
        "source_id": it.get("source_id"),
        "owner_team": sanitize_owner_team(it.get("owner_team")) or "",
        "pointer": normalize_pointer(it.get("pointer")),
        "text": (it.get("text") or "")[:_SNIP_LEN],
        "source_label": (it.get("source_label") or "").strip(),
        "entities": _parse_entities(it.get("entities")),
    }


def build_relation_candidates(items: list[dict]) -> list[dict]:
    """从 items 算出可写入 relations 的跨团队候选（已校验 provenance）。"""
    active = [dict(x) for x in items if not x.get("blocked")]
    # entity -> team -> items
    by_entity: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for it in active:
        ot = sanitize_owner_team(it.get("owner_team"))
        if not ot or ot == "外部媒体":
            continue
        row = _item_row(it)
        for name in row["entities"]:
            if len(name) < _MIN_ENTITY_LEN:
                continue
            by_entity[name][ot].append(row)

    raw_cands: list[dict] = []
    for entity, team_map in by_entity.items():
        teams = sorted(team_map.keys())
        if len(teams) < 2:
            continue
        if not cross_team_provenance_ok(active, entity, teams):
            continue
        all_items: list[dict] = []
        for t in teams:
            all_items.extend(team_map[t])
        item_ids = sorted({x["id"] for x in all_items if x.get("id") is not None})
        team_facts = []
        sources: list[str] = []
        for t in teams:
            snippets = []
            srcs = []
            for row in team_map[t][: _MAX_SNIPPETS * 2]:
                if row["text"] and row["text"] not in snippets:
                    snippets.append(row["text"])
                if row["source_label"] and row["source_label"] not in srcs:
                    srcs.append(row["source_label"])
                if len(snippets) >= _MAX_SNIPPETS:
                    break
            team_facts.append({"team": t, "item_ids": [x["id"] for x in team_map[t] if x.get("id")], "snippets": snippets[:_MAX_SNIPPETS], "sources": srcs[:3]})
            sources.extend(s for s in srcs if s not in sources)
        title = entity
        # 若同一组 item 上还有更长的共现实体，合并标题
        co = _coentities(all_items, entity)
        if co:
            title = f"{entity} · {co}" if entity not in co else co
        raw_cands.append({
            "title": title,
            "teams": teams,
            "weak": False,
            "suggested_label": _suggest_label(teams, team_facts),
            "team_facts": team_facts,
            "sources": sources[:6],
            "item_ids": item_ids,
            "provenance_ok": True,
        })

    return _dedupe_candidates(raw_cands)


def _coentities(items: list[dict], primary: str) -> str:
    counts: dict[str, int] = defaultdict(int)
    for row in items:
        for n in row.get("entities") or []:
            if n != primary and len(n) >= 2:
                counts[n] += 1
    if not counts:
        return ""
    return max(counts.items(), key=lambda x: (x[1], len(x[0])))[0]


def _suggest_label(teams: list[str], team_facts: list[dict]) -> str:
    """仅作 LLM 参考 hint；不写入 relation.label（避免清一色「各知一半」）。"""
    counts = [len(tf.get("snippets") or []) for tf in team_facts]
    if not any(counts):
        return ""
    if all(c >= 1 for c in counts):
        return "（参考：若两团队事实互补、无冲突，可用「同一件事，两个部门各知一半」）"
    return "（参考：若仅一方有记录，可用「一方接触，另一方用得上」）"


_MAX_CANDIDATES_FOR_LLM = 28


def _cap_candidates(cands: list[dict]) -> list[dict]:
    """限制喂给 LLM 的候选数量，避免 entity 共现淹没编辑判断。"""
    cands.sort(key=lambda c: (-len(c.get("item_ids") or []), -(len(c.get("teams") or [])), c.get("title") or ""))
    return cands[:_MAX_CANDIDATES_FOR_LLM]


def _dedupe_candidates(cands: list[dict]) -> list[dict]:
    """去掉 item 集合高度重叠的重复候选。"""
    cands.sort(key=lambda c: (-len(c.get("item_ids") or []), c.get("title") or ""))
    kept: list[dict] = []
    seen_ids: list[set] = []
    for c in cands:
        ids = set(c.get("item_ids") or [])
        if any(len(ids & old) / max(1, len(ids | old)) > 0.85 for old in seen_ids):
            continue
        kept.append(c)
        seen_ids.append(ids)
    return _cap_candidates(kept)


def _title_match(a: str, b: str) -> bool:
    """宽松匹配（仅 dedupe / 诊断）；candidate 挂载用 _strict_title_match。"""
    aa = (a or "").strip().lower()
    bb = (b or "").strip().lower()
    if not aa or not bb:
        return False
    if aa == bb or aa in bb or bb in aa:
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    return bool(pa & pb)


def _strict_title_match(a: str, b: str) -> bool:
    """严格匹配：禁止单 entity 猜测挂载（如仅「豆包」撞上另一张卡）。"""
    aa = (a or "").strip().lower()
    bb = (b or "").strip().lower()
    if not aa or not bb:
        return False
    if aa == bb:
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    if not pa or not pb:
        return False
    if pa == pb:
        return True
    if len(pa) == 1 or len(pb) == 1:
        return False
    sa, sb = (pa, pb) if len(pa) <= len(pb) else (pb, pa)
    return bool(len(sa) < len(sb) and sa <= sb)


def _facts_detail(tf: dict) -> str:
    team = tf.get("team") or ""
    snips = tf.get("snippets") or []
    if snips:
        return f"{team}记录：{snips[0]}"
    return f"{team}记录：（见来源）"


def _items_index(items: list[dict]) -> dict:
    return {x["id"]: x for x in items if x.get("id") is not None}


def _build_evidence(cand: dict, items_by_id: dict[int, dict]) -> list[dict]:
    """从 candidate.team_facts 展开为可回溯 evidence 列表。"""
    evidence: list[dict] = []
    seen: set = set()
    for tf in cand.get("team_facts") or []:
        team = tf.get("team") or ""
        snippets = list(tf.get("snippets") or [])
        for iid in tf.get("item_ids") or []:
            if iid in seen:
                continue
            seen.add(iid)
            row = items_by_id.get(iid) or {}
            snippet = snippets.pop(0) if snippets else (row.get("text") or "")[:_SNIP_LEN]
            item_team = sanitize_owner_team(row.get("owner_team")) or team
            evidence.append({
                "item_id": iid,
                "source_id": row.get("source_id"),
                "team": item_team,
                "snippet": snippet,
                "quote": snippet,
                "source_label": row.get("source_label") or "",
                "pointer": row.get("pointer") or "",
            })
    return evidence


def _relation_type_from_rel(rel: dict, cand: dict | None = None) -> str:
    label = (rel.get("label") or (cand or {}).get("suggested_label") or "").strip()
    if "两个部门" in label or "各知一半" in label:
        return "cross_team_sync"
    if "一方接触" in label:
        return "cross_team_gap"
    return "cross_team_sync"


def _attach_relation_assets(rel: dict, cand: dict, items_by_id: dict[int, dict]) -> dict:
    """将 candidate 的结构化依据写入 relation（JSON 资产层）。"""
    rel = dict(rel)
    rel["item_ids"] = list(cand.get("item_ids") or [])
    rel["evidence"] = _build_evidence(cand, items_by_id)
    rel["provenance_ok"] = bool(cand.get("provenance_ok", True))
    rel["relation_type"] = _relation_type_from_rel(rel, cand)
    rel["confidence"] = 0.6 if _suggested_teams(rel) else 0.95
    rel.pop("_candidate_item_ids", None)
    return rel


def skeleton_relation(cand: dict) -> dict:
    """仅作 fallback；正常路径以 LLM 叙事为主。"""
    details = [_facts_detail(tf) for tf in cand.get("team_facts") or []]
    teams = list(cand.get("teams") or [])
    body = f"{'、'.join(teams)}本期均有与「{cand.get('title')}」相关的记录。"
    return {
        "label": "",
        "weak": bool(cand.get("weak")),
        "title": cand.get("title") or "",
        "body": body,
        "details": details,
        "sources": list(cand.get("sources") or []),
        "teams": teams,
    }


def _attach_title_match(a: str, b: str) -> bool:
    """挂载 candidate：先 strict，再要求至少两个 entity 交集（避免「豆包」误挂）。"""
    if _strict_title_match(a, b):
        return True
    pa = _title_entities(a)
    pb = _title_entities(b)
    if len(pa) < 2 or len(pb) < 2:
        return False
    return len(pa & pb) >= 2


def _find_candidate(
    title: str,
    candidates: list[dict],
    used: set[int],
    *,
    strict: bool = True,
) -> tuple[dict | None, int | None]:
    for i, c in enumerate(candidates):
        if i in used:
            continue
        ct = c.get("title") or ""
        if strict:
            if _strict_title_match(title, ct):
                return c, i
        elif _attach_title_match(title, ct):
            return c, i
    return None, None


def _find_candidate_for_rel(
    title: str,
    candidates: list[dict],
    used: set[int],
) -> tuple[dict | None, int | None]:
    """先 strict 匹配，再宽松匹配（双 entity 以上，避免单字猜测）。"""
    cand, ci = _find_candidate(title, candidates, used, strict=True)
    if cand is not None:
        return cand, ci
    return _find_candidate(title, candidates, used, strict=False)


def _suggested_teams(rel: dict) -> list[str]:
    out: list[str] = []
    for t in rel.get("teams") or []:
        s = str(t).strip()
        if s.startswith("→") or s.startswith("->"):
            out.append(s)
    return out


def _solid_teams(rel: dict) -> list[str]:
    out: list[str] = []
    for t in rel.get("teams") or []:
        s = str(t).strip()
        if s.startswith("→") or s.startswith("->"):
            continue
        ot = sanitize_owner_team(s.lstrip("→").lstrip("->").strip()) or s
        if ot and ot not in out:
            out.append(ot)
    return out


def _teams_from_evidence(evidence: list[dict]) -> list[str]:
    teams: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        t = sanitize_owner_team(e.get("team")) or ""
        if t and t not in seen:
            teams.append(t)
            seen.add(t)
    return teams


def _sources_from_evidence(evidence: list[dict]) -> list[str]:
    sources: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        sl = (e.get("source_label") or "").strip()
        if sl and sl not in seen:
            sources.append(sl)
            seen.add(sl)
    return sources


def _details_from_evidence(evidence: list[dict], *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for e in evidence:
        team = sanitize_owner_team(e.get("team")) or ""
        snip = (e.get("snippet") or e.get("quote") or "").strip()
        if not snip:
            continue
        line = f"{team}记录：{snip}" if team else snip
        if line in seen:
            continue
        seen.add(line)
        out.append(line)
        if len(out) >= limit:
            break
    return out


def _align_relation_from_evidence(rel: dict, *, suggested: list[str] | None = None) -> dict:
    """事实 teams/sources 来自 evidence；保留 → 建议团队（虚线 dep）。"""
    rel = dict(rel)
    evidence = list(rel.get("evidence") or [])
    suggested = list(suggested or [])
    if not evidence:
        rel["teams"] = _solid_teams(rel) + [t for t in suggested if t not in rel.get("teams", [])]
        return rel
    allowed = set(_teams_from_evidence(evidence))
    rel["teams"] = _teams_from_evidence(evidence) + [t for t in suggested if t not in _teams_from_evidence(evidence)]
    rel["sources"] = _sources_from_evidence(evidence)
    kept: list[str] = []
    for d in rel.get("details") or []:
        m = _DETAIL_TEAM.match(str(d).strip())
        if m:
            ot = sanitize_owner_team(m.group(1).strip())
            if ot and ot in allowed:
                kept.append(d)
        else:
            kept.append(d)
    if not kept:
        kept = _details_from_evidence(evidence)
    rel["details"] = kept[:8]
    return normalize_relation_team_badges(rel, suggest_extra_solid=editorial_weak(rel))


def _facts_consistent(rel: dict) -> bool:
    evidence = list(rel.get("evidence") or [])
    if not evidence:
        return True
    ev_teams = _teams_from_evidence(evidence)
    rel_teams = [sanitize_owner_team(str(t).lstrip("→").lstrip("->").strip()) or "" for t in rel.get("teams") or []]
    rel_teams = [t for t in rel_teams if t and not str(t).startswith("→")]
    if rel_teams != ev_teams:
        return False
    ev_sources = _sources_from_evidence(evidence)
    rel_sources = [str(s).strip() for s in rel.get("sources") or [] if str(s).strip()]
    return rel_sources == ev_sources


def _dedupe_relations_by_title(rels: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for r in rels:
        key = (r.get("title") or "").strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def _finalize_relation_facts(rels: list[dict]) -> list[dict]:
    """有 evidence 的须 facts 一致；无 evidence 但含 → 建议团队的卡保留。"""
    out: list[dict] = []
    for r in rels:
        if not isinstance(r, dict):
            continue
        if r.get("evidence"):
            aligned = _align_relation_from_evidence(r, suggested=_suggested_teams(r))
            if _facts_consistent(aligned):
                out.append(aligned)
        elif editorial_weak(r) or _suggested_teams(r):
            nr = normalize_relation_team_badges(
                dict(r),
                suggest_extra_solid=editorial_weak(r),
            )
            nr.setdefault("needs_review", True)
            nr["status"] = "needs_review"
            out.append(nr)
    return _dedupe_relations_by_title(out)


def merge_relations_from_candidates(draft: dict, candidates: list[dict], items: list[dict]) -> dict:
    """LLM 精选 + candidate 挂 evidence；虚线卡可无 evidence；事实字段以 evidence 为准。"""
    data = dict(draft)
    llm_rels = list(data.get("relations") or [])
    used_cand: set[int] = set()
    merged: list[dict] = []
    items_by_id = _items_index(items)

    for r in llm_rels:
        if not isinstance(r, dict):
            continue
        rel = dict(r)
        suggested = _suggested_teams(rel)
        cand, ci = _find_candidate_for_rel(rel.get("title") or "", candidates, used_cand)
        if cand is not None:
            used_cand.add(ci)
            rel = _attach_relation_assets(rel, cand, items_by_id)
            rel = _align_relation_from_evidence(rel, suggested=suggested)
            if _facts_consistent(rel):
                merged.append(rel)
        elif editorial_weak(rel) or suggested:
            rel = normalize_relation_team_badges(rel, suggest_extra_solid=True)
            rel["needs_review"] = True
            rel["status"] = "needs_review"
            rel.setdefault("evidence", [])
            merged.append(rel)

    data["relations"] = merged
    from .relation_verify import verify_relations_narratives

    data["relations"] = verify_relations_narratives(data["relations"])
    data = filter_draft_relations(data, items)
    data["relations"] = _finalize_relation_facts(data.get("relations") or [])
    from .issue_verify import verify_issue_draft

    return verify_issue_draft(data, items)


def prepare_draft_bundle(con, issue_id: int, slug: str) -> dict:
    """供 main/preview_job 调用的 draft 输入包。"""
    from . import db

    merge_teams = db.draft_teams_for_issue(con, issue_id)
    item_rows = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    team_cards = []
    for team in merge_teams:
        row = con.execute(
            "SELECT card_json FROM cards WHERE issue_id=? AND team=? AND status='approved'",
            (issue_id, team),
        ).fetchone()
        if row and row["card_json"]:
            try:
                team_cards.append(json.loads(row["card_json"]))
            except json.JSONDecodeError:
                pass
    external = [
        dict(x)
        for x in con.execute(
            """SELECT owner_team AS team, stype, zone, level, kind, text, entities,
                      source_label, source_labels, channel
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL AND stype='T7'""",
            (issue_id,),
        )
    ]
    names = []
    for x in item_rows:
        for n in _parse_entities(x.get("entities")):
            e = con.execute("SELECT first_issue FROM entities WHERE name=?", (n,)).fetchone()
            if not e or e["first_issue"] == slug:
                names.append(n)
    names = list(dict.fromkeys(names))[:40]
    return {
        "team_cards": team_cards,
        "relation_candidates": build_relation_candidates(item_rows),
        "item_rows": item_rows,
        "external_items": external,
        "first_names": names,
    }
