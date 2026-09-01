"""Relation blocker 分类测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_classify import (
    CAT_NARRATIVE,
    CAT_REAL_MISSING,
    CAT_WEAK,
    CAT_WRONG,
    ACTION_BLOCK,
    ACTION_DOWNGRADE_WEAK,
    classify_relation,
)


def test_narrative_only_from_details():
    rel = {
        "title": "面壁智能 · 詹杨帆",
        "weak": False,
        "label": "一方接触，另一方用得上",
        "teams": ["编辑部", "硅谷 BD 团队"],
        "details": [
            "编辑部记录：端云协同。",
            "硅谷 BD 团队记录：终端路径。",
        ],
        "evidence": [{"item_id": 1, "team": "编辑部"}],
    }
    items = [
        {"id": 1, "owner_team": "编辑部", "blocked": 0, "entities": ["詹杨帆", "面壁智能"], "text": "x"},
    ]
    c = classify_relation(rel, items)
    assert c is not None
    assert c.category in (CAT_NARRATIVE, CAT_WRONG, CAT_WEAK)
    assert c.recommended_action in (ACTION_DOWNGRADE_WEAK, ACTION_BLOCK)


def test_real_missing_text_but_not_entity():
    rel = {
        "title": "豆包 · 字节跳动",
        "weak": False,
        "teams": ["编辑部", "视频号团队"],
        "details": ["编辑部记录：x", "视频号团队记录：y"],
        "evidence": [{"item_id": 1, "team": "编辑部"}],
        "provenance_ok": True,
    }
    items = [
        {"id": 1, "owner_team": "编辑部", "blocked": 0, "entities": ["豆包"], "text": "豆包更新"},
        {"id": 2, "owner_team": "视频号团队", "blocked": 0, "entities": [], "text": "字节跳动旗下豆包短视频合作"},
    ]
    c = classify_relation(rel, items)
    assert c is not None
    assert "视频号团队" in c.missing_teams
