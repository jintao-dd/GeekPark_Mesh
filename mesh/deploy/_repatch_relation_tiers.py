#!/usr/bin/env python3
"""Re-run Preview relation path (Decision→Gate→Writer) with existing draft narratives.

Uses audit decisions + current relation title/body/details — no draft LLM, no decision LLM.
Validates decision_tier propagation into draft_json on real tmesh data.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

from app import db
from app.relation_candidates import build_relation_candidates, prepare_draft_bundle
from app.relation_decision import assign_candidate_ids, build_relations_two_phase


def _decisions_from_audit(audit: dict) -> list[dict]:
    out: list[dict] = []
    for row in audit.get("candidate_ledger") or audit.get("rows") or []:
        if not isinstance(row, dict):
            continue
        dec = (row.get("llm_decision") or row.get("decision_outcome") or "").strip().lower()
        out.append({
            "candidate_id": row.get("candidate_id"),
            "decision": dec,
            "label": row.get("llm_label") or row.get("label") or "",
            "relation_type": row.get("relation_type"),
            "decision_tier": row.get("decision_tier"),
            "reason": row.get("llm_reason") or row.get("gate_reason") or "",
            "evidence_refs": row.get("llm_evidence_refs") or [],
        })
    return out


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    dry = "--dry-run" in sys.argv

    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    issue_id = int(row["id"])
    draft = json.loads(row["draft_json"] or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    decisions = _decisions_from_audit(audit)
    if not decisions:
        con.close()
        raise SystemExit("no decisions in draft audit")

    writings = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        cid = (r.get("candidate_id") or "").strip()
        if not cid:
            continue
        writings.append({
            "candidate_id": cid,
            "title": r.get("title") or "",
            "body": r.get("body") or "",
            "details": list(r.get("details") or []),
        })

    bundle = prepare_draft_bundle(con, issue_id, slug)
    cands = assign_candidate_ids(build_relation_candidates(bundle["item_rows"]))

    shell = dict(draft)
    shell.pop("relations", None)
    shell.pop("_relations_reader", None)
    shell.pop("_relations_backlog", None)
    shell.pop("_relation_decision_audit", None)

    out = build_relations_two_phase(
        shell,
        cands,
        bundle["item_rows"],
        team_cards=bundle["team_cards"],
        decisions=decisions,
        writings=writings,
    )

    rels = out.get("relations") or []
    tiers = {}
    for r in rels:
        t = r.get("decision_tier") or "null"
        tiers[t] = tiers.get(t, 0) + 1

    report = {
        "slug": slug,
        "n_relations": len(rels),
        "tier_counts": tiers,
        "missing_tier": sum(1 for r in rels if not r.get("decision_tier")),
        "n_reader": len(out.get("_relations_reader") or []),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if dry:
        con.close()
        return

    payload = json.dumps(out, ensure_ascii=False)
    reader_rels = out.get("_relations_reader") or [
        r for r in rels if isinstance(r, dict) and r.get("reader_visible")
    ]
    reader_payload = json.dumps({**out, "relations": reader_rels}, ensure_ascii=False)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
        (payload, reader_payload, stamp, issue_id),
    )
    con.commit()
    con.close()
    print("draft_updated")


if __name__ == "__main__":
    main()
