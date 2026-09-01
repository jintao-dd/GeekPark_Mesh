"""Phase 3 relation narrative verify 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_verify import line_grounded, verify_relation_narrative


def _rel_with_evidence(**extra):
    base = {
        "title": "飞书 · WorkBuddy",
        "teams": ["商业化团队", "编辑部"],
        "evidence": [
            {
                "item_id": 853,
                "source_id": 25,
                "team": "商业化团队",
                "snippet": "飞书合作待PR部门走正规流程对接（接触中）",
                "pointer": "15:02",
            },
            {
                "item_id": 789,
                "source_id": 23,
                "team": "编辑部",
                "snippet": "WorkBuddy 选题待确认",
                "pointer": "",
            },
        ],
    }
    base.update(extra)
    return base


def test_normal_narrative_kept():
    rel = _rel_with_evidence(
        body="商业化团队与编辑部本期均有与飞书、WorkBuddy 相关的记录。",
        details=["商业化团队记录：飞书合作待PR部门走正规流程对接（接触中）"],
    )
    out = verify_relation_narrative(rel)
    assert "飞书" in out["body"] or "WorkBuddy" in out["body"]
    assert any("飞书合作" in str(d) for d in out["details"])


def test_partial_overflow_trimmed():
    rel = _rel_with_evidence(
        body="商业化团队与 SAP 对谈项目已取消，飞书合作待 PR 对接。",
        details=[
            "商业化团队记录：飞书合作待PR部门走正规流程对接（接触中）",
            "外部媒体：字节 8/25 发布豆包工作，TRAE 并入豆包体系，影响全行业。",
        ],
    )
    assert not line_grounded(
        "外部媒体：字节 8/25 发布豆包工作，TRAE 并入豆包体系，影响全行业。",
        rel,
    )
    out = verify_relation_narrative(rel)
    blob = " ".join(str(x) for x in out["details"])
    assert "字节" not in blob and "TRAE" not in blob
    assert "飞书合作" in blob


def test_no_evidence_downgraded():
    rel = {
        "title": "完全编造 · 某公司",
        "teams": ["编辑部"],
        "body": "编辑部已与某公司签署独家战略合作。",
        "details": ["编辑部记录：独家战略合作已签署"],
        "evidence": [],
    }
    out = verify_relation_narrative(rel)
    assert out.get("needs_review") is True
    assert out["details"] == ["编辑部记录：独家战略合作已签署"]
    assert "独家战略合作" in out["body"]
