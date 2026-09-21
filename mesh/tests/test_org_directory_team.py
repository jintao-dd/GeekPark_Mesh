"""Org directory team/dept member listing."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.feishu_hands import org_directory as od
from app.agent.dept_team_map import load_dept_team_map


DEPTS = [
    {
        "name": "品牌创意部",
        "open_department_id": "od-brand",
        "parent_department_id": "0",
        "member_count": 1,
    },
    {
        "name": "创意视频",
        "open_department_id": "od-video",
        "parent_department_id": "od-brand",
        "member_count": 2,
    },
    {
        "name": "品牌设计",
        "open_department_id": "od-design",
        "parent_department_id": "od-brand",
        "member_count": 1,
    },
]

PEOPLE = [
    {
        "name": "张山山",
        "open_id": "ou_ss",
        "employee_no": "G-007",
        "enterprise_email": "",
        "job_title": "VP",
        "department_ids": ["od-brand"],
    },
    {
        "name": "闫晓龙",
        "open_id": "ou_xl",
        "employee_no": "",
        "enterprise_email": "",
        "job_title": "编辑",
        "department_ids": ["od-video"],
    },
    {
        "name": "彭康林",
        "open_id": "ou_49",
        "employee_no": "49",
        "enterprise_email": "",
        "job_title": "",
        "department_ids": ["od-design"],
    },
    {
        "name": "李源",
        "open_id": "ou_ly",
        "employee_no": "",
        "enterprise_email": "",
        "job_title": "记者",
        "department_ids": ["od-media"],
    },
]


def test_resolve_brand_creative_query_hits_dept_tree():
    ids, label = od.resolve_org_scope("品牌创意", DEPTS)
    assert ids is not None
    assert "od-brand" in ids and "od-video" in ids and "od-design" in ids
    assert "品牌创意" in label or "品牌创意部" in label


def test_resolve_who_is_on_team_strips_tail():
    ids, _ = od.resolve_org_scope("品牌创意有谁", DEPTS)
    assert ids is not None
    assert "od-video" in ids


def test_search_directory_lists_brand_members():
    load_dept_team_map(force=True)

    def fake_load(*, force=False):
        return DEPTS, PEOPLE

    with mock.patch.object(od, "load_directory", fake_load):
        env = od.search_directory("品牌创意有谁", max_results=20)
    assert env.ok
    titles = [it.get("title") for it in (env.items or [])]
    assert "张山山" in titles
    assert "闫晓龙" in titles
    assert "彭康林" in titles
    assert "李源" not in titles


def test_search_directory_design_only():
    def fake_load(*, force=False):
        return DEPTS, PEOPLE

    with mock.patch.object(od, "load_directory", fake_load):
        env = od.search_directory("品牌设计", max_results=20)
    titles = [it.get("title") for it in (env.items or [])]
    assert "彭康林" in titles
    assert "张山山" not in titles


def test_member_names_for_scope_brand_tree():
    def fake_load(*, force=False):
        return DEPTS, PEOPLE

    with mock.patch.object(od, "load_directory", fake_load):
        names = od.member_names_for_scope("品牌创意", limit=20)
    assert "张山山" in names
    assert "闫晓龙" in names
    assert "彭康林" in names
    assert "李源" not in names


def test_asker_teammates_stays_in_leaf_department():
    """直属「创新技术」时，不把同级的创意视频算进我们团队。"""
    depts = [
        {
            "name": "品牌创意部",
            "open_department_id": "od-brand",
            "parent_department_id": "0",
            "member_count": 1,
        },
        {
            "name": "创新技术",
            "open_department_id": "od-tech",
            "parent_department_id": "od-brand",
            "member_count": 2,
        },
        {
            "name": "创意视频",
            "open_department_id": "od-video",
            "parent_department_id": "od-brand",
            "member_count": 1,
        },
    ]
    people = [
        {"name": "杜锦涛", "open_id": "ou_djt", "department_ids": ["od-tech"], "job_title": ""},
        {"name": "同事甲", "open_id": "ou_a", "department_ids": ["od-tech"], "job_title": ""},
        {"name": "闫晓龙", "open_id": "ou_xl", "department_ids": ["od-video"], "job_title": "编辑"},
        {"name": "张山山", "open_id": "ou_ss", "department_ids": ["od-brand"], "job_title": "VP"},
    ]

    def fake_load(*, force=False):
        return depts, people

    with mock.patch.object(od, "load_directory", fake_load):
        names, label = od.asker_teammates("ou_djt", limit=20)
    assert label == "创新技术"
    assert "杜锦涛" in names
    assert "同事甲" in names
    assert "闫晓龙" not in names
    assert "张山山" not in names


def test_person_search_includes_department():
    depts = DEPTS + [
        {
            "name": "媒体业务",
            "open_department_id": "od-media",
            "parent_department_id": "0",
            "member_count": 1,
        }
    ]

    def fake_load(*, force=False):
        return depts, PEOPLE

    with mock.patch.object(od, "load_directory", fake_load):
        env = od.search_directory("李源", max_results=5)
    assert env.ok
    hits = [it for it in (env.items or []) if it.get("title") == "李源"]
    assert hits
    assert "部门:媒体业务" in str(hits[0].get("snippet") or "")
    assert "记者" in str(hits[0].get("snippet") or "")
