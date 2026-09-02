#!/usr/bin/env python3
"""对照 code candidates vs published relations，找遗漏。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import (
    build_relation_candidates,
    _strict_title_match,
    _attach_title_match,
    merge_relations_from_candidates,
)
from app.relation_gate import issue_publish_blockers


def _title_key(t: str) -> str:
    return (t or "").strip().lower()


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit("not found")
    issue_id = row["id"]
    pub = json.loads(row["published_json"] or "{}")
    draft = json.loads(row["draft_json"] or "{}")
    rels = pub.get("relations") or []
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    con.close()

    cands = build_relation_candidates(items)
    pub_titles = [(r.get("title") or "").strip() for r in rels if isinstance(r, dict)]

    matched_cands: list[dict] = []
    unmatched_cands: list[dict] = []
    used = set()
    for i, c in enumerate(cands):
        ct = c.get("title") or ""
        hit = False
        for pt in pub_titles:
            if _strict_title_match(ct, pt) or _attach_title_match(ct, pt):
                matched_cands.append({"candidate": ct, "published": pt, "teams": c.get("teams")})
                hit = True
                break
        if not hit:
            unmatched_cands.append({
                "title": ct,
                "teams": c.get("teams"),
                "item_ids": c.get("item_ids"),
                "sources": (c.get("sources") or [])[:3],
                "snippets": [
                    (tf.get("team"), (tf.get("snippets") or [""])[0][:80])
                    for tf in (c.get("team_facts") or [])[:2]
                ],
            })

    # draft 里 LLM 原始 relations（merge 前难取；看 draft 当前即 merge 后）
    # 用空 candidates 重跑 merge 无法还原 LLM；改查 draft relations 标题
    draft_rels = draft.get("relations") or []
    draft_titles = [(r.get("title") or "").strip() for r in draft_rels]

    # 高价值遗漏：双团队 provenance_ok 且未被 strict/attach 匹配
    strong_miss = []
    for u in unmatched_cands:
        teams = u.get("teams") or []
        if len(teams) >= 2:
            strong_miss.append(u)

    blockers = issue_publish_blockers(pub, items)

    report = {
        "slug": slug,
        "n_items": len(items),
        "n_candidates": len(cands),
        "n_published_relations": len(rels),
        "published_titles": pub_titles,
        "matched_candidates": matched_cands,
        "unmatched_candidates": unmatched_cands,
        "likely_missed_strong": strong_miss,
        "publish_blockers": blockers,
        "note": "likely_missed_strong = 代码算出跨团队候选但未出现在 published relations",
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
