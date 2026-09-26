"""Decision 一致性 + Writer team_facts 输入。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.relation_decision_consistency import check_decision_consistency
from app.relation_writer import to_writer_input


def test_parallel_label_allows_non_same_event_reason():
    """「非同一事件」正是 parallel 的合法理由，不得当矛盾。"""
    err = check_decision_consistency(
        label="同一赛道，各自在做",
        reason="商业化与视频号同处豆包议题但非同一事件",
        relation_type="parallel_tracks",
    )
    assert err is None


def test_parallel_label_blocks_no_relation_reason():
    err = check_decision_consistency(
        label="同一赛道，各自在做",
        reason="两团队完全无关系",
        relation_type="parallel_tracks",
    )
    assert err and "无关系" in err


def test_writer_input_includes_team_facts():
    inp = to_writer_input({
        "candidate_id": "c1",
        "label": "已联动",
        "teams": ["A", "B"],
        "evidence": [],
        "team_facts": [{"team": "A", "snippets": ["snippet A"]}],
        "relation_reason": "test",
    })
    assert inp["team_facts"][0]["team"] == "A"
    assert "sources" not in inp
