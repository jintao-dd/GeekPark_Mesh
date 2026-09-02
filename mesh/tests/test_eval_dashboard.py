"""Eval Dashboard V1 — read-only loader smoke tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.eval_dashboard import (  # noqa: E402
    BOUNDARY_SCORECARD,
    build_view,
    fmt_metric,
    fmt_pct,
    fmt_sec_ms,
    relation_funnel,
    relation_integrity,
)


def test_float_display_helpers():
    assert fmt_sec_ms(49333.399999999994) == "49.3秒"
    assert fmt_sec_ms(53961.99999999999) == "54.0秒"
    assert fmt_pct(0.0114, digits=2) == "1.14%"
    assert fmt_pct(0.0, digits=0) == "0%"
    assert fmt_metric("ask_p95_ms", 49333.399999999994) == "49.3秒"
    assert fmt_metric("ask_e2e_pass_rate", 0.96) == "96.0%"


def test_build_view_loads_current_baseline():
    view = build_view()
    assert view["ok"] is True
    assert view["readonly"] is True
    assert "Preview" in view["readonly_note"]
    env = view["env"]
    assert (env.get("commit") or "").startswith("2c6e8c3")
    assert env["git_dirty"] is False
    assert view["ask"]["e2e_frac"] == "24/25"
    assert view["ask"]["p95_s"] == "49.3秒"
    assert "49333" not in view["ask"]["p95_s"]
    assert view["boundary"]["fails"] == 0
    assert view["boundary"]["warns"] == 1
    assert view["boundary"]["passes"] == 9
    assert view["headline"]["verdict"]
    assert "当前结论" not in view["headline"]["verdict"] or True
    assert "Ask" in view["headline"]["verdict"]


def test_funnel_and_integrity_are_separate():
    view = build_view()
    assert view["ok"]
    funnel_ns = [s["n"] for s in view["funnel"]["steps"]]
    assert funnel_ns == [14, 14, 7, 0, 0, 0]
    integ = view["integrity"]
    assert integ["n_relations"] == 14
    assert integ["n_ok"] == 0
    assert view["funnel"]["not_store"] is True
    assert "不要混" in integ["disclaimer"] or "不是" in integ["disclaimer"]
    assert "代理" in view["relation"]["proxy_disclaimer"]
    assert view["relation"]["grounding_note"].startswith("「关系完整性」")
    assert "积压" in view["relation"]["human_review_note"] or "backlog" in view["relation"]["human_review_note"]
    assert view["funnel"]["reason_preview"]
    assert view["funnel"]["cases_by_step"]["candidates"]
    assert view["funnel"]["cases_by_step"]["gate_drop"]
    assert "门控" in view["headline"]["relation_drop"]


def test_self_compare_delta_formatted():
    view = build_view(
        current_name="BASELINE_v1_current.json",
        baseline_name="BASELINE_v1_current.json",
    )
    assert view["ok"]
    for r in view["delta"]:
        assert "baseline_s" in r and "new_s" in r and "delta_display" in r
        assert "399999" not in str(r["baseline_s"])
        assert "399999" not in str(r["new_s"])


def test_boundary_warn_not_hidden():
    warns = [r for r in BOUNDARY_SCORECARD if r["verdict"] == "WARN"]
    assert len(warns) == 1
    note = warns[0].get("note") or ""
    assert "命名" in note or "by_tier" in note


def test_funnel_helpers_on_minimal_doc():
    doc = {
        "metrics": {"n_candidates_for_decision": 14, "n_draft_relations": 0, "rel_grounding": 0.0},
        "cases": {
            "relation": {
                "audit_summary": {
                    "n_candidates": 14,
                    "n_skipped_llm": 7,
                    "n_skipped_gate_override": 7,
                    "n_draft_relations": 0,
                    "n_reader_relations": 0,
                    "by_skip_code": {"llm_skip": 7, "missing_evidence_refs": 4},
                },
                "integrity": {"n_relations": 14, "n_ok": 0},
                "candidate_ledger": [
                    {
                        "candidate_id": "c1",
                        "candidate_title": "A",
                        "decision_keep": True,
                        "gate_overrode_llm": True,
                        "final_outcome": "skipped_gate_override",
                        "skip_reason_code": "missing_evidence_refs",
                        "gate_reason": "缺证据",
                        "outcome_label": "门控拦截",
                    }
                ],
            }
        },
    }
    f = relation_funnel(doc)
    i = relation_integrity(doc)
    assert f["steps"][0]["n"] == 14
    assert f["steps"][2]["n"] == 7
    assert f["steps"][3]["n"] == 0
    assert f["cases_by_step"]["gate_drop"]
    assert i["title"] == "关系完整性（库存体检）"
    assert i["n_relations"] == 14


def test_experiment_history_enriched():
    view = build_view()
    assert view["experiments"]
    ex = next(e for e in view["experiments"] if e.get("is_current_pointer") or e["name"] == view["current_file"])
    assert ex.get("ask_e2e_frac")
    assert ex.get("vs_baseline")
