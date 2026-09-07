"""关系 Gold v2 烟测：按 expect 分派；claim_* 只校验契约标签，不接入 Claim 模型。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.owner_guard import relation_team_supported
from app.relation_continue import relation_fingerprint
from app.relation_gate import filter_ungrounded_relations, relation_fails_grounding
from eval.relation_gold_lib import (
    BASELINE_PATH,
    GOLD_V2,
    build_baseline,
    load_gold,
    validate_gold,
    write_baseline,
)


def _by_id():
    return {c["id"]: c for c in load_gold()}


def test_gold_v2_loads_and_schema():
    cases = load_gold()
    assert len(cases) >= 15
    errs = validate_gold(cases)
    assert errs == [], errs
    ids = {c["id"] for c in cases}
    for need in ("g01", "g02", "g05", "g09", "g12"):
        assert need in ids


def test_dispatch_keep():
    by_id = _by_id()
    keeps = [c for c in load_gold() if c["expect"] == "keep"]
    assert keeps
    for c in keeps:
        rel, items = c["rel"], c["items"]
        assert not relation_fails_grounding(rel, items), c["id"]
        # solid teams (if any) should be supported when evidence present
        if c["id"] == "g01":
            assert relation_team_supported(items, rel, "编辑部")
            assert relation_team_supported(items, rel, "硅谷 BD 团队")


def test_dispatch_drop():
    drops = [c for c in load_gold() if c["expect"] == "drop"]
    assert drops
    for c in drops:
        out, dropped = filter_ungrounded_relations({"relations": [c["rel"]]}, c["items"])
        assert c["rel"]["title"] in dropped, c["id"]
        assert out["relations"] == [], c["id"]


def test_dispatch_team_via_evidence():
    g4 = _by_id()["g04"]
    assert g4["expect"] == "team_via_evidence"
    assert relation_team_supported(g4["items"], g4["rel"], "编辑部")
    assert relation_team_supported(g4["items"], g4["rel"], "商业化团队")


def test_dispatch_fingerprint_stable():
    g5 = _by_id()["g05"]
    assert g5["expect"] == "fingerprint_stable"
    a = relation_fingerprint(g5["title_a"], g5["teams_a"])
    b = relation_fingerprint(g5["title_b"], g5["teams_b"])
    assert a == b


def test_dispatch_claim_labels_only():
    """Claim Validity 人工标签契约；本阶段不要求系统判定一致。"""
    claim_cases = [c for c in load_gold() if c["expect"] in ("claim_valid", "claim_invalid")]
    assert len(claim_cases) >= 6
    for c in claim_cases:
        claim = c["claim"]
        assert isinstance(claim["valid"], bool)
        assert (c["expect"] == "claim_valid") == claim["valid"], c["id"]
        assert (c.get("claim") or {}).get("reason")


def test_claim_baseline_records_lexical_not_as_validity():
    payload = build_baseline()
    assert payload["n_claim_cases"] >= 6
    # 至少存在「gold invalid 但 lexical 可能仍过」或明确分列字段
    for row in payload["cases"]:
        assert "gold_claim_valid" in row
        assert "gold_claim_invalid" in row
        assert "current_line_grounded" in row
        assert "current_gate_result" in row
        assert row["gold_claim_valid"] is not row["gold_claim_invalid"]
        # 禁止把字段混成同一布尔
        assert "line_grounded≠claim_valid" in (row.get("note") or "")

    path = write_baseline()
    assert path == BASELINE_PATH
    disk = json.loads(path.read_text(encoding="utf-8"))
    assert disk["n_claim_cases"] == payload["n_claim_cases"]

    # 关键：存在 claim_invalid 且 line_grounded True 的案例（证据存在≠论断成立）
    overclaims = [
        r
        for r in disk["cases"]
        if r["gold_claim_invalid"] and r["current_line_grounded"]
    ]
    assert overclaims, "expected at least one claim_invalid that still passes lexical grounding"


def test_v1_still_present_for_compat():
    v1 = ROOT / "eval" / "relation_gold_v1.jsonl"
    assert v1.is_file()
    assert GOLD_V2.is_file()
