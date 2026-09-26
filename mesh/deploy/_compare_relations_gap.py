#!/usr/bin/env python3
"""对比：代码候选 vs draft relations vs published relations，找遗漏。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import (
    build_relation_candidates,
    merge_relations_from_candidates,
    _strict_title_match,
    _attach_title_match,
)
from app.owner_guard import _title_entities


def _match_any(a: str, b: str) -> bool:
    return _strict_title_match(a, b) or _attach_title_match(a, b)


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    issue = con.execute(
        "SELECT id, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not issue:
        print(json.dumps({"error": "not found"}))
        return
    issue_id = issue["id"]
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    con.close()

    pub = json.loads(issue["published_json"] or "{}")
    draft_raw = json.loads(issue["draft_json"] or "{}")

    # draft 里 LLM 原始 relations（merge 前难取；用 re-merge 近似：先取 draft relations 若带 evidence 则是 merge 后）
    pub_rels = pub.get("relations") or []
    draft_rels = draft_raw.get("relations") or []

    cands = build_relation_candidates(items)

    used_pub: set[int] = set()
    matched_pub: list[dict] = []
    for r in pub_rels:
        t = r.get("title") or ""
        hit = None
        for i, c in enumerate(cands):
            if i in used_pub:
                continue
            if _match_any(t, c.get("title") or ""):
                used_pub.add(i)
                hit = c
                break
        matched_pub.append({"title": t, "candidate": (hit or {}).get("title"), "matched": hit is not None})

    missed_from_cands = []
    for i, c in enumerate(cands):
        if i in used_pub:
            continue
        ct = c.get("title") or ""
        teams = c.get("teams") or []
        missed_from_cands.append({
            "title": ct,
            "teams": teams,
            "n_items": len(c.get("item_ids") or []),
            "provenance_ok": c.get("provenance_ok"),
            "suggested_label": (c.get("suggested_label") or "")[:80],
        })

    # 若把 draft relations 当作 LLM 输出 re-merge（items 不变）看会剩多少
    # 注意：draft 已是 merge 后；尝试从 items+空 relations 无法还原 LLM 原始列表
    # 改：对每个 missed candidate 检查 items 里是否两团队都有 entity 记录
    enriched_miss = []
    for m in missed_from_cands:
        ct = m["title"]
        ent = list(_title_entities(ct))
        primary = ent[0] if ent else ct.split("·")[0].strip()
        team_items = {}
        for it in items:
            if it.get("blocked"):
                continue
            ents = it.get("entities") or "[]"
            if isinstance(ents, str):
                try:
                    ents = json.loads(ents)
                except Exception:
                    ents = []
            names = {str(x).lower() for x in ents}
            hit = any(e.lower() in names or e.lower() in (it.get("text") or "").lower() for e in ent) or primary.lower() in names
            if hit:
                ot = (it.get("owner_team") or "").strip()
                if ot:
                    team_items.setdefault(ot, []).append(it.get("id"))
        enriched_miss.append({**m, "teams_with_items": {k: len(v) for k, v in team_items.items()}})

    out = {
        "slug": slug,
        "n_items": len(items),
        "n_candidates": len(cands),
        "n_published_relations": len(pub_rels),
        "published_titles": [r.get("title") for r in pub_rels],
        "published_matched_candidates": matched_pub,
        "missed_candidates": enriched_miss,
        "missed_count": len(missed_from_cands),
        "candidate_titles_all": [c.get("title") for c in cands],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
