"""Attribution Verify 单元测试。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.attribution import PROVENANCE_MANUAL, PROVENANCE_SEGMENT
from app.attribution_verify import (
    narrative_team_from_label,
    scan_draft,
    scan_items,
)


def test_narrative_team_from_label():
    assert narrative_team_from_label("硅谷 BD 团队建联记录 · 与面壁") == "硅谷 BD 团队"
    assert narrative_team_from_label("编辑部沟通记录 · 与面壁") == "编辑部"


def test_scan_items_blocks_invalid_provenance():
    items = [
        {
            "id": 1, "owner_team": "编辑部", "owner_provenance": "llm_hint",
            "source_team": "内容中心·数据聚合", "blocked": 0, "source_label": "",
        },
    ]
    r = scan_items(items)
    assert r.blockers
    assert not r.ok


def test_scan_items_manual_must_match_pick():
    items = [
        {
            "id": 2, "owner_team": "硅谷 BD 团队", "owner_provenance": PROVENANCE_MANUAL,
            "source_team": "编辑部 · 沟通记录", "blocked": 0, "source_label": "",
        },
    ]
    r = scan_items(items)
    assert any("manual" in e for e in r.blockers)


def test_scan_items_narrative_mismatch_is_flag_not_blocker():
    items = [
        {
            "id": 3, "owner_team": "编辑部", "owner_provenance": PROVENANCE_SEGMENT,
            "source_team": "内容中心·数据聚合", "blocked": 0,
            "source_label": "硅谷 BD 团队建联记录", "pointer": "面壁—詹杨帆",
        },
    ]
    r = scan_items(items)
    assert r.ok
    assert r.narrative_flags
    assert not any("narrative" in b for b in r.blockers)


def test_scan_draft_evidence_team_must_match_item():
    items = [
        {"id": 10, "owner_team": "编辑部", "blocked": 0, "source_label": ""},
    ]
    draft = {
        "relations": [{
            "title": "测试 · 实体",
            "teams": ["编辑部"],
            "evidence": [{"item_id": 10, "team": "硅谷 BD 团队", "snippet": "x"}],
        }]
    }
    r = scan_draft(draft, items)
    assert any("≠ item.owner_team" in e for e in r.blockers)


def test_scan_draft_blocks_blocked_evidence():
    items = [
        {"id": 11, "owner_team": "编辑部", "blocked": 1, "source_label": ""},
    ]
    draft = {
        "relations": [{
            "title": "测试",
            "evidence": [{"item_id": 11, "team": "编辑部"}],
        }]
    }
    r = scan_draft(draft, items)
    assert any("blocked item" in e for e in r.blockers)
