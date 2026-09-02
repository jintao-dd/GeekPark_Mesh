#!/usr/bin/env python3
"""Prod relation pipeline health check."""
from __future__ import annotations

import inspect
import sys

from app.relation_candidates import build_relation_candidates
from app.relation_decision import apply_evidence_gate
from app.relation_decision_consistency import check_decision_consistency, normalize_relation_label
from app.relation_display import reader_visible
from app import llm


def main() -> None:
    assert hasattr(llm, "build_relation_decisions_with_coverage")
    src = inspect.getsource(apply_evidence_gate)
    assert "gate_warning" in src
    assert "_skip(GATE_CODE_DECISION_INCONSISTENT" not in src
    cand_src = open("/srv/mesh/app/relation_candidates.py", encoding="utf-8").read()
    assert "_build_routing_candidates" in cand_src
    prompt = open("/srv/mesh/app/prompts/issue_relation_decisions.md", encoding="utf-8").read()
    assert "先选标签" in prompt or "默认倾向 keep" in prompt

    err = check_decision_consistency(
        label="同一赛道，各自在做",
        reason="同处议题但非同一事件",
        relation_type="parallel_tracks",
    )
    assert err is None, err
    assert normalize_relation_label("同一条赛道，各自在做") == "同一赛道，各自在做"

    cand = {
        "candidate_id": "c1",
        "title": "可灵",
        "teams": ["Global Partnership 团队", "→ 编辑部"],
        "weak": True,
        "candidate_kind": "routing",
        "routing_targets": ["编辑部"],
        "item_ids": [10],
        "team_facts": [{"team": "Global Partnership 团队", "item_ids": [10], "snippets": ["GP"]}],
    }
    decisions = [{
        "candidate_id": "c1",
        "decision": "keep",
        "label": "一方接触，另一方用得上",
        "relation_type": "one_sided",
        "decision_tier": "watch",
        "reason": "GP有接触编辑部可对齐",
        "evidence_refs": [10],
    }]
    items = [{
        "id": 10, "source_id": 1, "owner_team": "Global Partnership 团队",
        "pointer": "x", "entities": '["可灵"]', "text": "GP", "source_label": "GP", "blocked": 0,
    }]
    approved, audit = apply_evidence_gate(decisions, [cand], items)
    assert len(approved) == 1, audit["rows"][0]

    cands = build_relation_candidates([
        {"id": 1, "source_id": 10, "owner_team": "编辑部", "pointer": "a", "entities": '["破壳创智"]', "roles": "[]", "text": "a", "source_label": "e", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "Global Partnership 团队", "pointer": "b", "entities": '["破壳创智"]', "roles": '["编辑部"]', "text": "GP，编辑部用得上", "source_label": "g", "blocked": 0},
    ])
    kinds = {c.get("candidate_kind") for c in cands}
    assert "cooccurrence" in kinds and "routing" in kinds, kinds

    assert reader_visible({"decision_tier": "strong", "evidence": [{}], "title": "t", "body": "b"}) is True
    assert reader_visible({"decision_tier": "watch", "evidence": [{}], "title": "t", "body": "b"}) is False

    print("PROD_RELATION_OK")
    print("containers_checked", sys.argv[1] if len(sys.argv) > 1 else "web")


if __name__ == "__main__":
    main()
