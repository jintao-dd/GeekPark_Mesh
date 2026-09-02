#!/usr/bin/env python3
"""Analyze whether 一方接触 labels are justified by evidence structure."""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import build_relation_candidates, candidate_build_stats, prepare_draft_bundle


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
    con = db.connect()
    issue = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    draft = json.loads(issue["draft_json"] or "{}")
    con.close()

    items = bundle["item_rows"]
    cands = build_relation_candidates(items)
    stats = candidate_build_stats(items)
    audit = draft.get("_relation_decision_audit") or {}
    ledger = audit.get("candidate_ledger") or audit.get("rows") or []
    by_id = {c.get("candidate_id"): c for c in cands}

    owners = Counter()
    for it in items:
        if it.get("blocked"):
            continue
        from app.aggregator import sanitize_owner_team
        ot = sanitize_owner_team(it.get("owner_team")) or "?"
        owners[ot] += 1

    kind_c = Counter(c.get("candidate_kind") or "cooccurrence" for c in cands)
    cooc = [c for c in cands if c.get("candidate_kind") != "routing"]
    route = [c for c in cands if c.get("candidate_kind") == "routing"]

    backlog = [r for r in ledger if r.get("final_outcome") == "backlog" or (
        r.get("gate_outcome") == "keep" and r.get("draft_kept")
    )]
    if not backlog:
        backlog = [r for r in ledger if r.get("gate_outcome") == "keep"]

    label_c = Counter((r.get("llm_label") or "") for r in backlog)
    evidence_team_n = []
    justified = []
    for r in backlog:
        cid = r.get("candidate_id")
        cand = by_id.get(cid) or {}
        teams = r.get("teams") or cand.get("teams") or []
        solid = [t for t in teams if not str(t).startswith(("→", "->"))]
        arrow = [t for t in teams if str(t).startswith(("→", "->"))]
        n_ev = r.get("n_evidence") or len(r.get("llm_evidence_refs") or [])
        kind = cand.get("candidate_kind") or "cooccurrence"
        label = r.get("llm_label") or ""
        ok_one_sided = kind == "routing" or len(solid) <= 1 or bool(arrow)
        evidence_team_n.append(len(solid))
        justified.append({
            "id": cid,
            "title": r.get("candidate_title"),
            "label": label,
            "tier": r.get("decision_tier"),
            "kind": kind,
            "solid_teams": solid,
            "arrow_teams": arrow,
            "n_evidence": n_ev,
            "one_sided_structure": ok_one_sided,
            "label_matches_structure": (
                ("一方接触" in label or "海外" in label) == ok_one_sided
                or (not ok_one_sided and "一方接触" not in label)
            ),
        })

    out = {
        "slug": slug,
        "n_items": len(items),
        "items_by_owner": dict(owners),
        "candidate_stats": stats,
        "candidate_by_kind": dict(kind_c),
        "n_cooccurrence": len(cooc),
        "cooccurrence_titles": [
            {"title": c.get("title"), "teams": c.get("teams"), "n_items": len(c.get("item_ids") or [])}
            for c in cooc
        ],
        "n_routing": len(route),
        "backlog_label_counts": dict(label_c),
        "backlog_n": len(backlog),
        "backlog_structure": justified,
        "n_one_sided_structure": sum(1 for j in justified if j["one_sided_structure"]),
        "n_two_team_structure": sum(1 for j in justified if not j["one_sided_structure"]),
        "verdict": (
            "本期候选以 routing（roles/文本「用得上」）为主；"
            "双向共现极少，故「一方接触，另一方用得上」占多数是数据结构决定的，不是标签库偏置 alone。"
            if len(route) >= len(cooc) * 3
            else "双向与单边混合，需逐条核对。"
        ),
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
