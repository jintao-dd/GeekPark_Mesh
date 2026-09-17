"""17 标签 × Gate 路径：标签库覆盖与单边放行。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.edm import FLAG_COLORS
from app.relation_decision import apply_evidence_gate
from app.relation_decision_consistency import (
    _LABEL_TYPES,
    infer_relation_type,
    normalize_relation_label,
)


def test_all_flag_colors_have_label_types():
    missing = [lb for lb in FLAG_COLORS if lb not in _LABEL_TYPES]
    assert not missing, f"missing _LABEL_TYPES: {missing}"


def test_label_alias_normalized():
    assert normalize_relation_label("同一条赛道，各自在做") == "同一赛道，各自在做"


def test_gate_accepts_canonical_and_alias_parallel_label():
    """同一赛道标签（含别名）在共享锚点双边证据下可 keep。"""
    items = [
        {"id": 1, "source_id": 10, "owner_team": "商业化团队", "pointer": "a", "entities": '["蚂蚁"]', "text": "蚂蚁稿件合作推进中", "source_label": "商业化", "blocked": 0},
        {"id": 2, "source_id": 11, "owner_team": "编辑部", "pointer": "b", "entities": '["蚂蚁"]', "text": "蚂蚁灵光具身智能现场接触", "source_label": "编辑部", "blocked": 0},
    ]
    for label in ("同一赛道，各自在做", "同一条赛道，各自在做"):
        cand = {
            "candidate_id": "c1",
            "title": "蚂蚁",
            "teams": ["商业化团队", "编辑部"],
            "item_ids": [1, 2],
            "team_facts": [
                {"team": "商业化团队", "item_ids": [1], "snippets": ["蚂蚁稿件合作推进中"]},
                {"team": "编辑部", "item_ids": [2], "snippets": ["蚂蚁灵光具身智能现场接触"]},
            ],
        }
        decisions = [{
            "candidate_id": "c1",
            "decision": "keep",
            "label": label,
            "relation_type": "parallel_tracks",
            "decision_tier": "parallel",
            "reason": "同一蚂蚁主体上两队各有动作，并行可参考",
            "evidence_refs": [1, 2],
        }]
        approved, audit = apply_evidence_gate(decisions, [cand], items)
        assert len(approved) == 1, (label, audit["rows"][0].get("gate_reason_code"))


def test_one_sided_single_team_allowed_as_backlog():
    """单边/海外仅 1 实线团队：2026-09 改为允许成卡但降级为 watch backlog，不进入读者页。"""
    one_sided_labels = [
        "一方接触，另一方用得上",
        "一方有需求，另一方尚未接触",
        "海外新发现，国内尚未接触",
        "一方报道了，另一方在接触",
    ]
    items = [{
        "id": 10, "source_id": 1, "owner_team": "Global Partnership 团队",
        "pointer": "可灵", "entities": '["可灵"]', "text": "GP 跟进可灵，编辑部用得上", "source_label": "GP", "blocked": 0,
    }]
    for label in one_sided_labels:
        rtype = infer_relation_type(label, "GP 有接触记录，编辑部可对齐")
        cand = {
            "candidate_id": "c1",
            "title": "可灵",
            "teams": ["Global Partnership 团队", "→ 编辑部"],
            "weak": True,
            "candidate_kind": "routing",
            "routing_targets": ["编辑部"],
            "item_ids": [10],
            "team_facts": [{"team": "Global Partnership 团队", "item_ids": [10], "snippets": ["GP 跟进可灵，编辑部用得上"]}],
        }
        decisions = [{
            "candidate_id": "c1",
            "decision": "keep",
            "label": label,
            "relation_type": rtype,
            "decision_tier": "watch",
            "reason": "GP 有接触记录，编辑部可对齐承接",
            "evidence_refs": [10],
        }]
        approved, audit = apply_evidence_gate(decisions, [cand], items)
        assert len(approved) == 1, (label, audit["rows"][0])
        assert approved[0]["decision_tier"] == "watch"
        assert approved[0].get("weak") is True
        assert audit["rows"][0].get("gate_decision") == "keep"
