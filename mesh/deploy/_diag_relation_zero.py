#!/usr/bin/env python3
"""诊断某期为何 relation candidate 很少 / 0 卡。"""
from __future__ import annotations
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import (
    _build_raw_candidates,
    build_relation_candidates,
    candidate_build_stats,
    prepare_draft_bundle,
)


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
    con = db.connect()
    issue = con.execute("SELECT id, draft_json, period_label FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        raise SystemExit(f"not found: {slug}")
    draft = json.loads(issue["draft_json"] or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    items = bundle["item_rows"]
    con.close()

    raw = _build_raw_candidates(items)
    stats = candidate_build_stats(items)
    cands = build_relation_candidates(items)

    teams = Counter(
        (it.get("owner_team") or "").strip()
        for it in items
        if not it.get("blocked")
    )
    entities = Counter()
    for it in items:
        for e in json.loads(it["entities"]) if isinstance(it.get("entities"), str) else (it.get("entities") or []):
            if e:
                entities[e] += 1

    out = {
        "slug": slug,
        "period_label": issue["period_label"],
        "kpi_relations": (draft.get("kpis") or [{}])[0] if False else next(
            (k.get("n") for k in (draft.get("kpis") or []) if "关系" in (k.get("label") or "")), None
        ),
        "draft_relations_count": len(draft.get("relations") or []),
        "items": {
            "n_active": len(items),
            "teams": dict(teams.most_common(20)),
            "n_distinct_entities": len(entities),
            "top_entities": entities.most_common(15),
        },
        "candidate_stats": stats,
        "candidates": [
            {
                "candidate_id": f"c{i+1}",
                "title": c.get("title"),
                "teams": c.get("teams"),
                "n_item_ids": len(c.get("item_ids") or []),
                "team_facts": [
                    {
                        "team": tf.get("team"),
                        "n_snippets": len(tf.get("snippets") or []),
                        "snippets_preview": (tf.get("snippets") or [])[:1],
                    }
                    for tf in (c.get("team_facts") or [])
                ],
            }
            for i, c in enumerate(cands)
        ],
        "decision_coverage": audit.get("decision_coverage"),
        "outcome_summary": audit.get("outcome_summary"),
        "ledger_titles": [
            (r.get("candidate_id"), r.get("candidate_title"), r.get("final_outcome"))
            for r in (audit.get("candidate_ledger") or audit.get("rows") or [])
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
