#!/usr/bin/env python3
"""诊断 Candidate → Decision 漏斗：14 在哪一步变成 2。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import build_relation_candidates, candidate_build_stats, prepare_draft_bundle
from app.relation_decision import assign_candidate_ids


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    issue = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        raise SystemExit(f"issue not found: {slug}")
    draft = json.loads(issue["draft_json"] or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    con.close()

    items = bundle["item_rows"]
    stats = candidate_build_stats(items)
    cands = assign_candidate_ids(build_relation_candidates(items))
    decisions = []
    for row in audit.get("rows") or audit.get("candidate_ledger") or []:
        if isinstance(row, dict):
            decisions.append(row.get("candidate_id"))

    out = {
        "slug": slug,
        "candidate_layer": {
            "n_items": len(items),
            "n_raw": stats["raw"],
            "n_deduped": stats["deduped"],
            "n_for_llm_cap": stats["for_llm"],
            "n_build_relation_candidates": len(cands),
            "titles": [(c.get("candidate_id"), c.get("title")) for c in cands],
        },
        "decision_layer": {
            "n_llm_decision_rows_in_audit": len(audit.get("rows") or []),
            "n_ledger": len(audit.get("candidate_ledger") or []),
            "decision_candidate_ids": decisions,
            "audit_stats_from_draft": {
                k: audit.get("outcome_summary", {}).get(k)
                for k in (
                    "n_candidates",
                    "n_candidates_raw",
                    "n_candidates_deduped",
                    "n_candidates_for_decision",
                )
                if audit.get("outcome_summary", {}).get(k) is not None
            },
            "audit_top_level": {
                k: audit.get(k)
                for k in (
                    "n_candidates",
                    "n_candidates_raw",
                    "n_candidates_deduped",
                    "n_candidates_for_decision",
                    "n_llm_decisions",
                )
                if audit.get(k) is not None
            },
        },
        "missing_from_decisions": [
            (c.get("candidate_id"), c.get("title"))
            for c in cands
            if c.get("candidate_id") not in set(decisions)
        ],
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
