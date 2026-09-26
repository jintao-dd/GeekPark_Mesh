"""Relation Gold v2 schema validator."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.relation_gold_lib import load_gold, validate_case, validate_gold


def test_validate_all_cases_clean():
    assert validate_gold() == []


def test_validate_rejects_claim_mismatch():
    bad = {
        "id": "bad1",
        "title": "x",
        "notes": "n",
        "expect": "claim_valid",
        "rel": {"title": "t", "body": "b", "evidence": []},
        "items": [],
        "claim": {"valid": False, "reason": "should mismatch"},
    }
    errs = validate_case(bad)
    assert any("inconsistent" in e for e in errs)


def test_validate_requires_unified_keys():
    bad = {"id": "bad2", "title": "t", "notes": "n", "expect": "keep"}
    errs = validate_case(bad)
    assert any("`rel`" in e for e in errs)
    assert any("`items`" in e for e in errs)
    assert any("`claim`" in e for e in errs)


def test_ids_unique_and_claim_coverage():
    cases = load_gold()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids))
    expects = {c["expect"] for c in cases}
    for need in (
        "keep",
        "drop",
        "team_via_evidence",
        "fingerprint_stable",
        "claim_valid",
        "claim_invalid",
    ):
        assert need in expects
