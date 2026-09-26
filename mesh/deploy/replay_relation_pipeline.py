#!/usr/bin/env python3
"""离线 replay：Decision → Gate → Writer(mock) → Display，不调 LLM、不跑要点卡。

用法:
  python deploy/replay_relation_pipeline.py --slug 2026-8-17
  python deploy/replay_relation_pipeline.py --audit eval/reports/relation_decisions_2026-8-17.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.relation_candidates import build_relation_candidates, prepare_draft_bundle
from app.relation_decision import apply_evidence_gate, assign_candidate_ids
from app.relation_decision_audit import build_human_report
from app.relation_display import attach_reader_flags, display_summary, split_relations_for_publish
from app.relation_writer import write_relations


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


def _mock_writings(objects: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for obj in objects:
        cid = obj.get("candidate_id")
        title = (obj.get("candidate_title") or "关系").strip()
        body = (obj.get("relation_reason") or title).strip()
        details = []
        for e in obj.get("evidence") or []:
            team = (e.get("team") or "").strip()
            snip = (e.get("snippet") or e.get("quote") or "").strip()
            if team and snip:
                details.append(f"{team}：{snip}")
        rows.append({
            "candidate_id": cid,
            "title": title,
            "body": body,
            "details": details[:8],
        })
    return rows


def replay(*, slug: str | None, audit_path: Path | None) -> dict:
    if audit_path and audit_path.exists():
        raw = json.loads(audit_path.read_text(encoding="utf-8"))
        audit_in = raw.get("_relation_decision_audit") or raw
        slug = slug or raw.get("slug") or "2026-8-17"
    else:
        audit_in = None
        slug = slug or "2026-8-17"

    from app import db

    con = db.connect()
    issue = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        con.close()
        raise SystemExit(f"issue not found: {slug}")
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    con.close()

    items = bundle["item_rows"]
    cands = assign_candidate_ids(build_relation_candidates(items))

    if audit_in:
        decisions = _decisions_from_audit(audit_in)
    else:
        draft = json.loads(
            db.connect().execute("SELECT draft_json FROM issues WHERE slug=?", (slug,)).fetchone()["draft_json"]
        )
        audit_in = draft.get("_relation_decision_audit") or {}
        decisions = _decisions_from_audit(audit_in)
        if not decisions:
            raise SystemExit("no decisions in audit; run preview first or pass --audit")

    objects, audit = apply_evidence_gate(decisions, cands, items)
    writings = _mock_writings(objects)
    rels, skipped = write_relations(objects, writings)

    rels = attach_reader_flags(rels)
    reader, backlog = split_relations_for_publish(rels)
    audit["display_summary"] = display_summary(rels)
    audit["n_draft_relations"] = len(rels)
    audit["n_reader_relations"] = len(reader)
    audit["n_backlog_relations"] = len(backlog)
    report = build_human_report(audit, rels)

    return {
        "slug": slug,
        "mode": "offline_replay",
        "outcome_summary": report["outcome_summary"],
        "display_summary": audit["display_summary"],
        "ledger_lines": report["ledger_lines"],
        "candidate_ledger": report["candidate_ledger"],
        "write_skipped": skipped,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--audit", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    out = replay(slug=args.slug, audit_path=args.audit)
    text = json.dumps(out, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.out}")
    else:
        print(text)


if __name__ == "__main__":
    main()
