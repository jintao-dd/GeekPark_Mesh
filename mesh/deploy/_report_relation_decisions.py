#!/usr/bin/env python3
"""输出 relation decision audit：每个 candidate 为何留/丢。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_decision_audit import build_human_report, finalize_decision_audit

KNOWN = [
    "Founder Park · AGI",
    "AGI Playground 对谈",
    "美团 · 生活服务",
    "千问 · Claude Opus 4.5",
    "豆包 · 字节跳动",
]


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    row = con.execute(
        "SELECT draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    con.close()
    if not row:
        raise SystemExit("not found")
    draft = json.loads(row["draft_json"] or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    rels = draft.get("relations") or []

    human = build_human_report(audit, rels)
    summary = human["outcome_summary"]

    known_out = []
    ledger_by_title: dict[str, dict] = {}
    for entry in human.get("candidate_ledger") or []:
        ct = (entry.get("candidate_title") or "").strip()
        if ct:
            ledger_by_title[ct] = entry

    for name in KNOWN:
        hit = None
        for r in rels:
            t = (r.get("title") or "").strip()
            cid = (r.get("candidate_id") or "").strip()
            if name in t or t in name or (r.get("candidate_title") or "").strip() == name:
                hit = r
                break
        row_match = None
        for ct, entry in ledger_by_title.items():
            if name.split("·")[0].strip() in ct or name in ct:
                row_match = entry
                break
        known_out.append({
            "name": name,
            "published": hit is not None,
            "published_title": (hit.get("title") or "").strip() if hit else None,
            "ledger": row_match,
            "n_evidence": len((hit or {}).get("evidence") or []),
        })

    report = {
        "slug": slug,
        "n_published_relations": len(rels),
        "outcome_summary": summary,
        "ledger_lines": human.get("ledger_lines") or [],
        "candidate_ledger": human.get("candidate_ledger") or [],
        "gate_overrides": human.get("gate_overrides") or [],
        "llm_skips": [
            {
                "candidate_id": r.get("candidate_id"),
                "candidate_title": r.get("candidate_title"),
                "llm_reason": r.get("llm_reason"),
                "review_hint": r.get("review_hint"),
            }
            for r in (human.get("llm_skips") or [])
        ],
        "published_rows": human.get("published_rows") or [],
        "known_problem_cards": known_out,
        "published_titles": [(r.get("title") or "").strip() for r in rels],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
