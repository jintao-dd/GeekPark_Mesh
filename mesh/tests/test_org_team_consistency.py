"""组织/业务队一致性：identity 与 db 归一不得分叉。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, ingest
from app.agent import identity as idn
from app.auth import _sanitize_feishu_display
from eval.check_org_team_consistency import run


def test_haiwai_in_ingest_teams():
    assert "海外拓展" in ingest.TEAMS


def test_normalize_no_split_brain_on_haiwai():
    assert db.normalize_team("海外拓展") == "海外拓展"
    assert idn.normalize_team("海外拓展") == "海外拓展"
    assert db.normalize_team("海外拓展") == idn.normalize_team("海外拓展")


def test_leaf_depts_still_map_to_brand():
    assert db.normalize_team("创新技术") == "品牌创意团队"
    assert idn.normalize_team("创新技术") == "品牌创意团队"
    assert db.normalize_team("创意视频") == idn.normalize_team("创意视频")


def test_non_business_excluded_from_identity():
    assert idn.normalize_team("其他") is None
    assert idn.normalize_team("外部媒体") is None
    assert idn.is_business_team("其他") is False


def test_sanitize_dirty_display():
    assert _sanitize_feishu_display("杜锦涛54564545") == "杜锦涛"
    assert _sanitize_feishu_display("杜锦涛") == "杜锦涛"
    assert _sanitize_feishu_display("Sean Shen") == "Sean Shen"


def test_org_team_consistency_script_passes():
    out = run()
    assert out["pass"], out
