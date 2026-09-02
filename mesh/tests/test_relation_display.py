"""Decision tier + draft/reader 分离。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_display import reader_visible, split_relations_for_publish


def test_watch_and_parallel_reader_visible():
    for tier in ("parallel", "watch", "strong"):
        rel = {
            "decision_tier": tier,
            "gate_would_cooccur": False,
            "title": "t",
            "body": "b",
            "evidence": [{}],
        }
        assert reader_visible(rel), tier


def test_skip_or_empty_not_visible():
    assert not reader_visible({
        "decision_tier": "skip", "title": "t", "body": "b", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "watch", "title": "t", "body": "", "evidence": [{}],
    })
    assert not reader_visible({
        "decision_tier": "watch", "title": "t", "body": "b", "evidence": [],
    })


def test_split_all_readable_go_reader():
    rels = [
        {"candidate_id": "c1", "decision_tier": "strong", "title": "A", "body": "a", "evidence": [{}]},
        {"candidate_id": "c2", "decision_tier": "parallel", "title": "B", "body": "b", "evidence": [{}]},
        {"candidate_id": "c3", "decision_tier": "watch", "title": "C", "body": "c", "evidence": [{}]},
    ]
    reader, backlog = split_relations_for_publish(rels)
    assert len(reader) == 3
    assert backlog == []
