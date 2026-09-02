"""Relation Writing Module 测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_writer import (
    LOCKED_FIELDS,
    merge_writing,
    relation_object_from_gate,
    strip_route_meta_copy,
    to_writer_input,
    write_relations,
)


def _sample_object():
    return relation_object_from_gate({
        "candidate_id": "c9",
        "candidate_title": "豆包 · 字节跳动",
        "label": "同一赛道，各自在做",
        "relation_type": "parallel_tracks",
        "decision_tier": "parallel",
        "teams": ["商业化团队", "视频号团队"],
        "sources": ["商业化周报", "视频号周数据"],
        "evidence": [
            {"item_id": 123, "team": "商业化团队", "snippet": "豆包 AI 手机判断", "source_label": "商业化周报"},
            {"item_id": 456, "team": "视频号团队", "snippet": "豆包收费视频", "source_label": "视频号周数据"},
        ],
        "item_ids": [123, 456],
        "weak": False,
        "provenance_ok": True,
        "decision_reason": "两团队分别围绕豆包开展动作",
    })


def test_writer_input_excludes_locked_mutation_surface():
    obj = _sample_object()
    inp = to_writer_input(obj)
    assert inp["candidate_id"] == "c9"
    assert inp["label"] == "同一赛道，各自在做"
    assert len(inp["evidence"]) == 2
    assert inp["relation_reason"] == "两团队分别围绕豆包开展动作"
    assert "sources" not in inp


def test_merge_writing_only_adds_narrative_fields():
    obj = _sample_object()
    writing = {
        "candidate_id": "c9",
        "title": "豆包（字节跳动）：两条通道各自在跟",
        "body": "商业化团队关注豆包终端价值，视频号团队产出豆包相关视频。",
        "details": [
            "商业化团队：豆包 AI 手机判断",
            "视频号团队：豆包收费视频",
        ],
        "label": "已联动",
        "teams": ["假团队"],
        "evidence": [],
    }
    rel = merge_writing(obj, writing)
    assert rel["title"] == writing["title"]
    assert rel["body"] == writing["body"]
    assert rel["label"] == "同一赛道，各自在做"
    assert rel["teams"] == ["商业化团队", "视频号团队"]
    assert len(rel["evidence"]) == 2
    for f in ("sources", "weak", "provenance_ok", "item_ids"):
        assert rel.get(f) == obj.get(f)
    assert rel["decision_tier"] == "parallel"
    assert rel["relation_type"] == "parallel_tracks"


def test_relation_object_from_gate_carries_decision_tier():
    obj = relation_object_from_gate({
        "candidate_id": "c1",
        "label": "已联动",
        "relation_type": "event_chain",
        "decision_tier": "strong",
        "teams": ["编辑部"],
        "evidence": [{"item_id": 1, "team": "编辑部", "snippet": "x"}],
    })
    assert obj["decision_tier"] == "strong"
    assert obj["relation_type"] == "event_chain"


def test_merge_writing_preserves_decision_tier_when_writer_omits():
    obj = _sample_object()
    writing = {
        "candidate_id": "c9",
        "title": "豆包两条线",
        "body": "各自推进。",
        "details": [],
        "decision_tier": "strong",
        "relation_type": "event_chain",
    }
    rel = merge_writing(obj, writing)
    assert rel["decision_tier"] == "parallel"
    assert rel["relation_type"] == "parallel_tracks"


def test_write_relations_skips_empty_body():
    obj = _sample_object()
    rels, skipped = write_relations([obj], writings=[{"candidate_id": "c9", "title": "x", "body": "", "details": []}])
    assert rels == []
    assert skipped[0]["reason"] == "missing_narrative_title_or_body"


def test_locked_fields_constant():
    assert "label" in LOCKED_FIELDS
    assert "decision_tier" in LOCKED_FIELDS
    assert "relation_type" in LOCKED_FIELDS
    assert "evidence" in LOCKED_FIELDS
    assert "title" not in LOCKED_FIELDS


def test_strip_route_meta_copy_investment_tails():
    assert strip_route_meta_copy(
        "张岩出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中，投资团队可承接。"
    ) == "张岩出现在 Global Partnership 团队 AGI Playground 前沿社活动嘉宾名单中。"
    assert strip_route_meta_copy(
        "Global Partnership 团队记录：新发现嘉宾：苏昊；对编辑部选题、投资团队可用"
    ) == "Global Partnership 团队记录：新发现嘉宾：苏昊"
    assert strip_route_meta_copy(
        "双方约定长期互相对接；国内编辑部选题可用得上。"
    ) == "双方约定长期互相对接"
    assert strip_route_meta_copy("赵越（仙工智能）关注中，投资团队用得上") == "赵越（仙工智能）关注中"
    # 不要吞掉分号前的事实动作
    assert "拟联系" in strip_route_meta_copy(
        "有意加入前沿社，先从邀请参加活动开始接触（拟联系）；对编辑部、投资团队可用"
    )
    assert "拟尽量取得联系" in strip_route_meta_copy(
        "尚未接触，拟尽量取得联系；对编辑部、投资团队可用"
    )
