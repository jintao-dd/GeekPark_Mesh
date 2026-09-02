#!/usr/bin/env python3
"""从 draft JSON（含 _relation_decision_audit）生成 candidate ledger 报告。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.relation_decision_audit import build_human_report


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "eval" / "reports" / "two_phase_draft_dump.json"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "eval" / "reports" / "relation_decisions_2026-8-17.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    if "audit" in raw:
        audit = raw["audit"]
        rels = raw.get("relations") or []
    else:
        audit = raw.get("_relation_decision_audit") or raw
        rels = raw.get("relations") or []
    report = build_human_report(audit, rels)
    payload = {
        "outcome_summary": report["outcome_summary"],
        "ledger_lines": report["ledger_lines"],
        "candidate_ledger": report["candidate_ledger"],
        "gate_overrides": report["gate_overrides"],
        "llm_skips": report["llm_skips"],
        "published_rows": report["published_rows"],
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    for line in report["ledger_lines"]:
        print(line)


if __name__ == "__main__":
    main()
