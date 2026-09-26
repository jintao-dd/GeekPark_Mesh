#!/usr/bin/env python3
"""items 里 roles 路由 vs entity 共现候选 对比。"""
from __future__ import annotations
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.aggregator import sanitize_owner_team
from app.relation_candidates import prepare_draft_bundle, _build_raw_candidates

TEAMS = {
    "编辑部", "商业化团队", "硅谷 BD 团队", "Global Partnership 团队", "英文站",
    "品牌创意团队", "社群", "投资团队", "音频播客团队", "视频号团队",
    "CEO / 总裁办", "CEO", "总裁办",
}
ROUTE_RE = re.compile(r"用得上|可供|对照|承接|联动|采访池|嘉宾|路由")


def _roles(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return [str(x).strip() for x in (raw or []) if str(x).strip()]


def _entities(raw) -> list[str]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return [str(x).strip() for x in (raw or []) if len(str(x).strip()) >= 2]


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
    con = db.connect()
    issue = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    con.close()
    items = bundle["item_rows"]

    entity_cands = _build_raw_candidates(items)

    routed: list[dict] = []
    text_routed: list[dict] = []
    for it in items:
        if it.get("blocked"):
            continue
        owner = sanitize_owner_team(it.get("owner_team")) or ""
        roles = _roles(it.get("roles"))
        ents = _entities(it.get("entities"))
        text = (it.get("text") or "").strip()
        role_teams = [r for r in roles if r in TEAMS and r != owner]
        for t in TEAMS:
            if t != owner and t in text and t not in role_teams:
                role_teams.append(t)
        if role_teams:
            routed.append({
                "id": it.get("id"),
                "owner": owner,
                "target_teams": role_teams,
                "entities": ents[:5],
                "text": text[:140],
                "roles": roles[:6],
            })
        if ROUTE_RE.search(text):
            text_routed.append({
                "id": it.get("id"),
                "owner": owner,
                "entities": ents[:5],
                "text": text[:160],
            })

    # 按 owner→target 聚合
    pairs: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for r in routed:
        for tgt in r["target_teams"]:
            pairs[(r["owner"], tgt)].append(r)

    pair_summary = [
        {
            "from_team": a,
            "to_team": b,
            "n_items": len(rows),
            "sample_entities": list({e for row in rows for e in row["entities"]})[:8],
            "sample_text": rows[0]["text"],
        }
        for (a, b), rows in sorted(pairs.items(), key=lambda x: -len(x[1]))
    ]

    print(json.dumps({
        "slug": slug,
        "entity_cooccurrence_candidates": len(entity_cands),
        "entity_candidate_titles": [c.get("title") for c in entity_cands],
        "items_with_role_or_text_routing": len(routed),
        "items_with_route_phrase_in_text": len(text_routed),
        "owner_to_target_pairs": len(pairs),
        "top_routing_pairs": pair_summary[:20],
        "route_phrase_samples": text_routed[:12],
        "gap_note": (
            "产品定义含「一方碰到、另一方用得上」，但 Candidate 仅做 entity 跨团队共现；"
            "roles/文本路由未进 relation_candidates"
        ),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
