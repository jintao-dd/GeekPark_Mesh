"""Unit tests for mesh_baseline metric calibration (v1.1)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from deploy.mesh_baseline import (  # noqa: E402
    _ask_bundle,
    _canonical_latency_ms,
    _rel_bundle,
    _token_from_usage,
    build_baseline,
    compare,
)


def test_token_no_double_count():
    assert _token_from_usage({"total_tokens": 100, "prompt_tokens": 60, "completion_tokens": 40}) == 100
    assert _token_from_usage({"prompt_tokens": 60, "completion_tokens": 40}) == 100
    assert _token_from_usage({}) == 0


def test_canonical_latency_prefers_e2e_not_double():
    q = {
        "latency": {"total_ms": 18151, "retrieve_ms": 200},
        "recall": {"latency_ms": 4},
    }
    assert _canonical_latency_ms(q) == (18151.0, "latency.total_ms")
    m, cases, _ = _ask_bundle({"questions": [
        {"id": "e1", "pass": True, "latency": {"total_ms": 100}, "recall": {"latency_ms": 5}, "usage": {"total_tokens": 10}},
        {"id": "e2", "pass": True, "latency": {"total_ms": 200}, "recall": {"latency_ms": 8}, "usage": {"prompt_tokens": 3, "completion_tokens": 7}},
    ]})
    assert m["ask_latency_n_samples"] == 2
    assert m["ask_token_total"] == 20  # 10 + 10, not 10+3+7 doubled
    assert m["ask_e2e_pass_rate"] == 1.0
    assert "ask_recall" not in m
    assert cases[0]["latency_ms"] == 100


def test_ask_naming_is_e2e_not_recall():
    m, _, _ = _ask_bundle({"questions": [
        {"id": "a", "pass": True, "recall": {"latency_ms": 10}},
        {"id": "b", "pass": False, "failure_layer": "retrieval", "recall": {"latency_ms": 20}},
    ]})
    assert m["ask_e2e_pass_rate"] == 0.5
    assert m["ask_n"] == 2


def test_rel_grounding_ratio_and_zero_relations():
    reg0 = {"checks": {
        "relation_integrity_draft": {
            "n_relations": 0, "n_ok": 0, "strong_no_evidence": 0, "blocked_leaks": 0,
        },
        "new_fields": {},
    }}
    m0, _, _ = _rel_bundle(reg0)
    assert m0["rel_grounding"] is None
    assert m0["rel_candidate_keep_rate_proxy"] is None

    reg = {
        "checks": {
            "relation_integrity_draft": {
                "n_relations": 10, "n_ok": 7, "strong_no_evidence": 2,
                "blocked_leaks": 0, "team_mismatch": 1,
            },
            "new_fields": {
                "n_draft_relations": 10,
                "n_draft_backlog": 1,
                "missing_decision_tier": [],
                "audit_summary": {
                    "n_candidates_for_decision": 40,
                    "n_skipped_gate_override": 5,
                    "n_published": 10,
                },
            },
            "pytest": {"ok": True, "tail": "100 passed in 1s"},
            "relation_pytest": {"ok": False, "tail": "2 failed, 8 passed"},
        }
    }
    m, cases, _ = _rel_bundle(reg)
    assert m["rel_grounding"] == 0.7
    assert m["rel_candidate_keep_rate_proxy"] == 0.25  # 10/40
    assert "rel_candidate_recall_proxy" not in m
    assert m["rel_decision_gate_accept_rate_proxy"] == round(10 / 15, 4)
    assert m["failure_rate"] is not None
    assert m["failure_rate"] > 0
    assert cases["integrity"]["n_ok"] == 7


def test_grounding_drop_is_blocker():
    base = {"metrics": {"ask_grounding": 1.0, "rel_grounding": 0.9, "rel_strong_no_evidence": 0}}
    new = {"metrics": {"ask_grounding": 0.95, "rel_grounding": 0.9, "rel_strong_no_evidence": 0}}
    rows, blocked = compare(base, new)
    assert blocked
    assert any(r["key"] == "ask_grounding" and r["status"] == "[BLOCKER]" for r in rows)


def test_build_includes_cases_and_proxy_notes():
    doc = build_baseline(
        ask_report={"questions": [
            {"id": "e1", "pass": True, "recall": {"latency_ms": 11}, "usage": {"total_tokens": 5}},
        ]},
        regression_report={
            "corpus": "golden",
            "slug": "2026-8-17",
            "checks": {
                "relation_integrity_draft": {
                    "n_relations": 2, "n_ok": 2, "strong_no_evidence": 0, "blocked_leaks": 0,
                },
                "new_fields": {
                    "n_draft_relations": 2,
                    "audit_summary": {"n_candidates_for_decision": 4},
                },
            },
        },
        relation_audit={"candidate_ledger": [{"candidate_id": "c1"}]},
        label="v1.1-test",
    )
    assert doc["schema"] == "mesh_baseline_v1.1"
    assert len(doc["cases"]["ask"]) == 1
    assert doc["cases"]["relation"]["candidate_ledger"][0]["candidate_id"] == "c1"
    assert any("ask_e2e_pass_rate" in x for x in doc["notes"]["proxy"])
    assert "git_dirty" in doc["env"]
    assert set(doc["hard_blockers"]["higher_is_better_must_not_drop"]) == {
        "ask_grounding", "rel_grounding",
    }


def test_retry_rate_null_without_n_retries():
    """有 llm_calls 但无 n_retries 字段 → retry_rate 必须是 null，不能伪造成 0。"""
    m, cases, extras = _ask_bundle({
        "questions": [
            {
                "id": "e1",
                "pass": True,
                "latency": {"total_ms": 100},
                "usage": {"total_tokens": 10, "llm_calls": 2},
            },
        ],
    })
    assert extras.get("retry_rate") is None
    assert cases[0].get("n_retries") is None


def test_retry_rate_zero_when_retries_explicit():
    _, _, extras = _ask_bundle({
        "questions": [
            {
                "id": "e1",
                "pass": True,
                "latency": {"total_ms": 100},
                "n_retries": 0,
                "n_llm_calls": 3,
                "usage": {"total_tokens": 10, "llm_calls": 3, "n_retries": 0},
            },
        ],
    })
    assert extras.get("retry_rate") == 0.0
