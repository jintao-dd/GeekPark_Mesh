"""Decision tier + draft/reader 分离 + Publish projection。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_display import (
    build_published_projection,
    draft_backlog_relations,
    is_reader_tier,
    reader_visible,
    split_relations_for_publish,
)


def test_only_strong_reader_visible():
    for tier in ("parallel", "watch"):
        rel = {
            "decision_tier": tier,
            "title": "t",
            "body": "b",
            "evidence": [{}],
        }
        assert not reader_visible(rel), tier
    assert reader_visible({
        "decision_tier": "strong",
        "title": "t",
        "body": "b",
        "evidence": [{}],
    })


def test_skip_or_incomplete_not_visible():
    assert not reader_visible({
        "decision_tier": "skip", "title": "t", "body": "b", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "strong", "title": "t", "body": "", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "strong", "title": "t", "body": "b", "evidence": [],
    })


def test_split_strong_to_reader_rest_backlog():
    rels = [
        {"candidate_id": "c1", "decision_tier": "strong", "title": "A", "body": "a", "evidence": [{}]},
        {"candidate_id": "c2", "decision_tier": "parallel", "title": "B", "body": "b", "evidence": [{}]},
        {"candidate_id": "c3", "decision_tier": "watch", "title": "C", "body": "c", "evidence": [{}]},
    ]
    reader, backlog = split_relations_for_publish(rels)
    assert len(reader) == 1
    assert reader[0]["candidate_id"] == "c1"
    assert reader[0]["reader_visible"] is True
    assert len(backlog) == 2
    assert all(not r["reader_visible"] for r in backlog)


def test_build_published_projection_strong_only_strips_internal():
    draft = {
        "title": "期",
        "relations": [
            {"decision_tier": "strong", "title": "S1", "body": "b", "evidence": [{"ref": "e1"}]},
            {"decision_tier": "strong", "title": "S2", "body": "b", "evidence": [{"ref": "e2"}]},
            {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{"ref": "e3"}]},
            {"decision_tier": "watch", "title": "W", "body": "b", "evidence": [{"ref": "e4"}]},
            {"decision_tier": "skip", "title": "X", "body": "b", "evidence": [{"ref": "e5"}]},
        ],
        "_relations_reader": [{"title": "leak"}],
        "_relations_backlog": [{"title": "b"}],
        "_relation_decision_audit": {"n": 1},
        "_stale": True,
        "kpis": [{"n": "99", "label": "可同步的关系"}],
    }
    pub = build_published_projection(draft)
    assert [r["title"] for r in pub["relations"]] == ["S1", "S2"]
    assert all(is_reader_tier(r) for r in pub["relations"])
    assert "_relations_reader" not in pub
    assert "_relations_backlog" not in pub
    assert "_relation_decision_audit" not in pub
    assert "_stale" not in pub
    assert pub["kpis"][0]["n"] == "2"
    # draft 未被原地修改
    assert len(draft["relations"]) == 5
    assert draft.get("_relations_reader")


def test_projection_ignores_false_reader_visible_flag():
    """业务语义以 decision_tier 为准，不信任被写坏的 reader_visible。"""
    draft = {
        "relations": [
            {
                "decision_tier": "parallel",
                "reader_visible": True,
                "title": "P",
                "body": "b",
                "evidence": [{}],
            },
            {
                "decision_tier": "strong",
                "reader_visible": False,
                "title": "S",
                "body": "b",
                "evidence": [{}],
            },
        ],
    }
    pub = build_published_projection(draft)
    assert [r["title"] for r in pub["relations"]] == ["S"]
    assert pub["relations"][0]["reader_visible"] is True


def test_draft_backlog_includes_parallel_watch_keeps_index():
    rels = [
        {"decision_tier": "strong", "title": "S", "body": "b", "evidence": [{}]},
        {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{}]},
        {"decision_tier": "watch", "title": "W", "body": "b", "evidence": [{}]},
        {"decision_tier": "skip", "title": "X", "body": "b", "evidence": [{}]},
        {"decision_tier": "strong", "title": "Incomplete", "body": "", "evidence": [{}]},
    ]
    backlog = draft_backlog_relations(rels)
    assert [x["rel"]["title"] for x in backlog] == ["P", "W", "Incomplete"]
    assert [x["index"] for x in backlog] == [1, 2, 4]
