"""Apply detail 行 neutralize（展示层，不改 owner）。"""
from __future__ import annotations

import json
import re

from .aggregator import sanitize_owner_team
from .narrative_clean import _DETAIL_ATTRIBUTION, clean_detail_line


def _detail_team(line: str) -> str | None:
    m = _DETAIL_ATTRIBUTION.match((line or "").strip())
    if not m:
        return None
    return sanitize_owner_team(m.group(1).strip())


def _owner_for_detail(line: str, rel: dict, items_by_id: dict[int, dict]) -> str:
    """用与 detail 行对齐的 evidence.item 的 owner_team 作事实来源。"""
    evidence = rel.get("evidence") or []
    details = rel.get("details") or []
    sline = (line or "").strip()

    # 优先：details 全列表中的行号 ↔ evidence 同 index
    try:
        idx = next(i for i, d in enumerate(details) if str(d).strip() == sline)
    except StopIteration:
        idx = -1
    if idx >= 0 and idx < len(evidence):
        row = items_by_id.get(evidence[idx].get("item_id")) or {}
        owner = sanitize_owner_team(row.get("owner_team"))
        if owner:
            return owner

    # 次选：仅有「X记录：」格式的 detail 子序列按 index 对齐
    team_details = [str(d).strip() for d in details if _detail_team(str(d))]
    if sline in team_details:
        tidx = team_details.index(sline)
        if tidx < len(evidence):
            row = items_by_id.get(evidence[tidx].get("item_id")) or {}
            owner = sanitize_owner_team(row.get("owner_team"))
            if owner:
                return owner

    return ""


def apply_detail_neutralize(draft: dict, items_by_id: dict[int, dict]) -> tuple[dict, dict]:
    """neutralize detail 行；返回 (draft, stats)。"""
    data = dict(draft)
    stats = {"neutralized": 0, "keep": 0, "relations_touched": 0, "samples": []}
    rels = []
    for rel in data.get("relations") or []:
        if not isinstance(rel, dict):
            rels.append(rel)
            continue
        rel = dict(rel)
        new_details = []
        changed = False
        for line in rel.get("details") or []:
            sline = str(line)
            owner = _owner_for_detail(sline, rel, items_by_id)
            r = clean_detail_line(sline, owner)
            new_details.append(r.cleaned if r.action == "neutralize" else sline)
            if r.action == "neutralize":
                stats["neutralized"] += 1
                changed = True
                if len(stats["samples"]) < 15:
                    stats["samples"].append({
                        "relation": rel.get("title"),
                        "before": r.original,
                        "after": r.cleaned,
                    })
            else:
                stats["keep"] += 1
        if changed:
            rel["details"] = new_details
            stats["relations_touched"] += 1
        rels.append(rel)
    data["relations"] = rels
    return data, stats


def sync_evidence_source_labels(draft: dict, items_by_id: dict[int, dict]) -> int:
    """evidence.source_label 与 item 对齐。"""
    n = 0
    for rel in draft.get("relations") or []:
        for ev in rel.get("evidence") or []:
            iid = ev.get("item_id")
            row = items_by_id.get(iid)
            if not row:
                continue
            sl = (row.get("source_label") or "").strip()
            if sl and ev.get("source_label") != sl:
                ev["source_label"] = sl
                n += 1
    return n
