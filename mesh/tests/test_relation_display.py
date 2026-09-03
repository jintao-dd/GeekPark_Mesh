"""Decision tier + Publish projection：进草稿即读者可见，按强度排序。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_display import (
    build_published_projection,
    draft_backlog_relations,
    indexed_relations_for_display,
    is_reader_tier,
    reader_visible,
    sort_relations_by_strength,
    split_relations_for_publish,
)


def test_all_keep_tiers_reader_visible_when_complete():
    for tier in ("strong", "parallel", "watch"):
        rel = {
            "decision_tier": tier,
            "title": "t",
            "body": "b",
            "evidence": [{}],
        }
        assert reader_visible(rel), tier
        assert is_reader_tier(rel), tier


def test_skip_or_incomplete_not_visible():
    assert not reader_visible({
        "decision_tier": "skip", "title": "t", "body": "b", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "strong", "title": "t", "body": "", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "parallel", "title": "t", "body": "b", "evidence": [],
    })


def test_split_all_complete_to_reader():
    rels = [
        {"candidate_id": "c1", "decision_tier": "strong", "title": "A", "body": "a", "evidence": [{}]},
        {"candidate_id": "c2", "decision_tier": "parallel", "title": "B", "body": "b", "evidence": [{}]},
        {"candidate_id": "c3", "decision_tier": "watch", "title": "C", "body": "c", "evidence": [{}]},
    ]
    reader, backlog = split_relations_for_publish(rels)
    assert len(reader) == 3
    assert [r["candidate_id"] for r in reader] == ["c1", "c2", "c3"]
    assert all(r["reader_visible"] for r in reader)
    assert backlog == []


def test_sort_relations_by_strength():
    rels = [
        {"decision_tier": "watch", "title": "W", "body": "b", "evidence": [{}]},
        {"decision_tier": "strong", "title": "S", "body": "b", "evidence": [{}]},
        {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{}]},
    ]
    ordered = sort_relations_by_strength(rels)
    assert [r["title"] for r in ordered] == ["S", "P", "W"]
    view = indexed_relations_for_display(rels)
    assert [x["rel"]["title"] for x in view] == ["S", "P", "W"]
    assert [x["index"] for x in view] == [1, 2, 0]


def test_build_published_projection_all_tiers_sorted_strips_internal():
    draft = {
        "title": "期",
        "relations": [
            {"decision_tier": "watch", "title": "W", "body": "b", "evidence": [{"ref": "e4"}]},
            {"decision_tier": "strong", "title": "S1", "body": "b", "evidence": [{"ref": "e1"}]},
            {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{"ref": "e3"}]},
            {"decision_tier": "strong", "title": "S2", "body": "b", "evidence": [{"ref": "e2"}]},
            {"decision_tier": "skip", "title": "X", "body": "b", "evidence": [{"ref": "e5"}]},
        ],
        "_relations_reader": [{"title": "leak"}],
        "_relations_backlog": [{"title": "b"}],
        "_relation_decision_audit": {"n": 1},
        "_stale": True,
        "kpis": [{"n": "99", "label": "可同步的关系"}],
    }
    pub = build_published_projection(draft)
    assert [r["title"] for r in pub["relations"]] == ["S1", "S2", "P", "W"]
    assert all(reader_visible(r) for r in pub["relations"])
    assert "_relations_reader" not in pub
    assert "_relations_backlog" not in pub
    assert "_relation_decision_audit" not in pub
    assert "_stale" not in pub
    assert pub["kpis"][0]["n"] == "4"
    assert len(draft["relations"]) == 5
    assert draft.get("_relations_reader")


def test_projection_trusts_completeness_not_false_flag():
    """可见性以卡完整为准；误写的 reader_visible 不影响投影。"""
    draft = {
        "relations": [
            {
                "decision_tier": "parallel",
                "reader_visible": False,
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
    assert [r["title"] for r in pub["relations"]] == ["S", "P"]
    assert all(r["reader_visible"] is True for r in pub["relations"])


def test_draft_backlog_only_incomplete():
    rels = [
        {"decision_tier": "strong", "title": "S", "body": "b", "evidence": [{}]},
        {"decision_tier": "parallel", "title": "P", "body": "b", "evidence": [{}]},
        {"decision_tier": "watch", "title": "W", "body": "b", "evidence": [{}]},
        {"decision_tier": "skip", "title": "X", "body": "b", "evidence": [{}]},
        {"decision_tier": "strong", "title": "Incomplete", "body": "", "evidence": [{}]},
    ]
    backlog = draft_backlog_relations(rels)
    assert [x["rel"]["title"] for x in backlog] == ["Incomplete"]
    assert [x["index"] for x in backlog] == [4]
