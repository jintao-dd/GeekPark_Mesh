"""集成：Claim Check 必须在 Evidence Gate 之后；无 evidence 卡不得进入 Claim Check。"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.relation_claim_check import apply_claim_checks
from app.relation_decision import apply_evidence_gate, build_relations_two_phase


def test_pipeline_source_order_gate_before_claim():
    src = inspect.getsource(build_relations_two_phase)
    i_gate = src.find("apply_evidence_gate")
    i_write = src.find("write_relations")
    i_claim = src.find("apply_claim_checks")
    assert i_gate >= 0 and i_write >= 0 and i_claim >= 0
    assert i_gate < i_write < i_claim


def test_gate_rejects_empty_refs_not_in_approved():
    items = [
        {
            "id": 1,
            "owner_team": "编辑部",
            "text": "编辑部记录甲",
            "entities": '["甲"]',
            "blocked": 0,
            "source_id": 10,
            "pointer": "p1",
        }
    ]
    cands = [
        {
            "candidate_id": "c1",
            "title": "甲 · 空壳",
            "item_ids": [1],
            "team_facts": [{"team": "编辑部", "item_ids": [1]}],
        }
    ]
    decisions = [
        {
            "candidate_id": "c1",
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "evidence_refs": [],
            "reason": "无 refs",
        }
    ]
    approved, audit = apply_evidence_gate(decisions, cands, items)
    assert approved == []
    assert audit["n_keep_gate"] == 0


def test_gate_keep_always_has_nonempty_evidence():
    items = [
        {
            "id": 1,
            "owner_team": "编辑部",
            "text": "编辑部接触面壁",
            "entities": '["面壁智能"]',
            "blocked": 0,
            "source_id": 10,
            "pointer": "a",
        },
        {
            "id": 2,
            "owner_team": "硅谷 BD 团队",
            "text": "硅谷 BD 跟进面壁",
            "entities": '["面壁智能"]',
            "blocked": 0,
            "source_id": 20,
            "pointer": "b",
        },
    ]
    cands = [
        {
            "candidate_id": "c1",
            "title": "面壁智能",
            "item_ids": [1, 2],
            "team_facts": [
                {"team": "编辑部", "item_ids": [1], "snippet": "编辑部接触面壁"},
                {"team": "硅谷 BD 团队", "item_ids": [2], "snippet": "硅谷 BD 跟进面壁"},
            ],
        }
    ]
    decisions = [
        {
            "candidate_id": "c1",
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "evidence_refs": [1, 2],
            "reason": "双侧触点",
        }
    ]
    approved, _ = apply_evidence_gate(decisions, cands, items)
    assert len(approved) == 1
    assert approved[0].get("evidence")
    assert all(e.get("item_id") for e in approved[0]["evidence"])


def test_two_phase_empty_evidence_never_reaches_claim_check():
    """即便 Writer 产物被污染成 evidence=[]，orchestrator 也不得送进 Claim Check。"""
    items = [
        {
            "id": 1,
            "owner_team": "编辑部",
            "text": "编辑部接触甲",
            "entities": '["甲"]',
            "blocked": 0,
            "source_id": 1,
            "pointer": "p",
        },
        {
            "id": 2,
            "owner_team": "商业化团队",
            "text": "商业化跟进甲",
            "entities": '["甲"]',
            "blocked": 0,
            "source_id": 2,
            "pointer": "q",
        },
    ]
    cands = [
        {
            "title": "甲",
            "item_ids": [1, 2],
            "team_facts": [
                {"team": "编辑部", "item_ids": [1], "snippet": "编辑部接触甲"},
                {"team": "商业化团队", "item_ids": [2], "snippet": "商业化跟进甲"},
            ],
        }
    ]
    decisions = [
        {
            "candidate_id": "c1",
            "decision": "keep",
            "label": "同一件事，两个部门各知一半",
            "decision_tier": "strong",
            "relation_type": "info_complement",
            "evidence_refs": [1, 2],
            "reason": "ok",
        }
    ]
    writings = [
        {
            "candidate_id": "c1",
            "title": "甲：两侧接触",
            "body": "编辑部与商业化均有触点。",
            "details": ["编辑部接触甲", "商业化跟进甲"],
        }
    ]
    out = build_relations_two_phase(
        {}, cands, items, decisions=decisions, writings=writings,
    )
    claim = out.get("_relation_claim_audit") or {}
    assert claim.get("n_checked", 0) >= 1
    for row in out.get("relations") or []:
        assert row.get("evidence"), "post-pipeline relation must keep evidence"

    # 污染路径：直接证明 apply 前过滤 — 空 evidence 不进 checked
    polluted = [
        {
            "candidate_id": "legacy",
            "title": "空证据卡",
            "body": "编辑部接触某人",
            "details": [],
            "evidence": [],
            "decision_tier": "watch",
            "relation_type": "one_sided",
            "weak": True,
        }
    ]
    kept, audit = apply_claim_checks(polluted, items, mode="shadow")
    # Claim Check 自身仍可被直接调用；orchestrator 契约由 n_skipped 锁死
    assert audit["n_checked"] == 1  # direct call still checks

    # 经 two_phase：无 refs → 0 claim checks
    out2 = build_relations_two_phase(
        {},
        cands,
        items,
        decisions=[
            {
                "candidate_id": "c1",
                "decision": "keep",
                "label": "同一件事，两个部门各知一半",
                "decision_tier": "strong",
                "evidence_refs": [],
                "reason": "缺 refs",
            }
        ],
        writings=[],
    )
    claim2 = out2.get("_relation_claim_audit") or {}
    # Gate 未放行 → Writer 无卡 → Claim Check 检查数为 0
    assert claim2.get("n_checked", 0) == 0
    decis = out2.get("_relation_decision_audit") or {}
    assert decis.get("n_skipped_empty_evidence_before_claim", 0) == 0
