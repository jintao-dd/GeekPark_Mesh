"""Feishu department → Mesh team map tests."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import dept_team_map as dtm
from app import db


def test_brand_creative_children_map_to_same_mesh_team():
    dtm.load_dept_team_map(force=True)
    for name in ("品牌创意部", "创意视频", "创新技术", "品牌设计", "海外拓展"):
        assert dtm.map_department_name(name) == "品牌创意团队", name


def test_brand_creative_ids():
    dtm.load_dept_team_map(force=True)
    assert (
        dtm.map_department_id("od-47a97215f774c63e3aad5dbc98b2a521")
        == "品牌创意团队"
    )  # 创新技术
    assert (
        dtm.map_department_id("od-a6227428b9e705a41cf142128fb1bfda")
        == "品牌创意团队"
    )  # 创意视频


def test_founder_park_and_media():
    dtm.load_dept_team_map(force=True)
    assert dtm.map_department_name("社区运营") == "社群"
    assert dtm.map_department_name("社区内容") == "社群"
    assert dtm.map_department_name("GeekPark 媒体业务") == "编辑部"
    assert dtm.map_department_name("商业化") == "商业化团队"
    assert dtm.map_department_name("变量资本-投资") == "投资团队"


def test_normalize_team_accepts_feishu_leaf_names():
    assert db.normalize_team("创意视频") == "品牌创意团队"
    assert db.normalize_team("品牌设计") == "品牌创意团队"
    assert db.normalize_team("创新技术") == "品牌创意团队"
    assert db.normalize_team("海外拓展") == "品牌创意团队"


def test_parent_walk_when_child_missing_from_runtime_lookup():
    # 仅提供 parent 链，子 id 不在 map 时仍可上溯（用假 id 测 walk）
    parent_lookup = {
        "od-fake-child": "od-3f2b9310260ff7673d0694fb43060515",  # → 品牌创意部
    }
    # fake child 不在 map；上溯到品牌创意部
    assert (
        dtm.map_department_id("od-fake-child", parent_lookup=parent_lookup)
        == "品牌创意团队"
    )
