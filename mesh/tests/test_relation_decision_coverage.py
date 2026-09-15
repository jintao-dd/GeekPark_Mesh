"""LLM decision coverage：漏答不得静默消失。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.llm import missing_decision_ids
from app.relation_decision import apply_evidence_gate, assign_candidate_ids
from app.relation_decision_audit import GATE_CODE_DECISION_MISSING, finalize_decision_audit


def _cand(cid: str, title: str) -> dict:
    return {
        "candidate_id": cid,
        "title": title,
        "teams": ["A", "B"],
        "item_ids": [1, 2],
        "team_facts": [
            {"team": "A", "item_ids": [1], "snippets": ["a"]},
            {"team": "B", "item_ids": [2], "snippets": ["b"]},
        ],
    }


def _items():
    return [
        {"id": 1, "source_id": 1, "owner_team": "A", "pointer": "p1", "entities": '["X"]', "text": "a", "source_label": "A", "blocked": 0},
        {"id": 2, "source_id": 2, "owner_team": "B", "pointer": "p2", "entities": '["X"]', "text": "b", "source_label": "B", "blocked": 0},
    ]


def test_missing_decision_ids_detected():
    cands = assign_candidate_ids([_cand("", "t1"), _cand("", "t2"), _cand("", "t3")])
    decisions = [{"candidate_id": "c1", "decision": "skip", "reason": "x", "evidence_refs": []}]
    assert missing_decision_ids(cands, decisions) == ["c2", "c3"]


def test_apply_evidence_gate_records_decision_missing_not_skip():
    cands = assign_candidate_ids([_cand("", "A"), _cand("", "B")])
    decisions = [{
        "candidate_id": "c1",
        "decision": "skip",
        "reason": "主动 skip",
        "evidence_refs": [],
    }]
    _, audit = apply_evidence_gate(
        decisions, cands, _items(), missing_ids={"c2"},
    )
    assert audit["n_candidates"] == 2
    assert audit["n_decision_missing"] == 1
    ledger = finalize_decision_audit(audit, [])["candidate_ledger"]
    by_id = {r["candidate_id"]: r for r in ledger}
    assert by_id["c2"]["final_outcome"] == "decision_missing"
    assert by_id["c2"]["gate_reason_code"] == GATE_CODE_DECISION_MISSING
    assert by_id["c2"]["final_outcome"] != "skipped_llm"
    assert by_id["c1"]["final_outcome"] == "skipped_llm"


def test_build_relation_decisions_batches_and_missing_only_retry(monkeypatch):
    """大批量 + 末尾截断时：分批合并，漏答只补 missing。"""
    from app import llm

    cands = assign_candidate_ids([_cand("", f"t{i}") for i in range(5)])
    calls: list[list[str]] = []

    def fake_call(system, user, max_tokens=0):
        # 从 user 里抽出本批 candidate_id
        ids = [c["candidate_id"] for c in cands if f'"candidate_id": "{c["candidate_id"]}"' in user
               or f'"candidate_id":"{c["candidate_id"]}"' in user]
        # 简化：按本批出现顺序取 id（prepare 会 json dump）
        import re
        ids = re.findall(r'"candidate_id":\s*"(c\d+)"', user)
        # 去重保序
        seen = set()
        ordered = []
        for cid in ids:
            if cid not in seen:
                seen.add(cid)
                ordered.append(cid)
        calls.append(ordered)
        # 前两批（batch_size=3 → 2 chunks）故意丢掉末尾，模拟截断
        drop_tail = len(calls) <= 2
        keep = ordered[:-1] if drop_tail and len(ordered) > 1 else ordered
        return {
            "relation_decisions": [
                {"candidate_id": cid, "decision": "skip", "reason": "x", "evidence_refs": []}
                for cid in keep
            ]
        }

    monkeypatch.setattr(llm, "call_json_compliant", fake_call)
    decisions, meta = llm.build_relation_decisions_with_coverage(
        cands, [], batch_size=3, max_retries=2,
    )
    assert meta["coverage_ok"] is True
    assert len(decisions) == 5
    assert missing_decision_ids(cands, decisions) == []
    # 至少有一次 missing-only 补全（调用次数 > 初始分批数）
    assert len(calls) > 2
    # 补全调用不应再塞全量 5 条
    assert all(len(c) < 5 for c in calls[2:])
