"""qa_structured 硬件过滤 SQL 回归。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, qa_structured


@pytest.fixture
def facts_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MESH_DB", path)
    db.init_db(seed=False)
    con = db.connect()
    con.execute(
        """INSERT INTO entity_team_facts
           (issue_slug, date_start, date_end, section, name, team, kind, group_title, snippet, source_hint)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        ("2026-8-17", "2026-08-01", "2026-08-31", "接触", "某硬件公司", "编辑部", "company",
         "智能硬件", "助听器方向", "T1"),
    )
    con.commit()
    yield con
    con.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_hardware_diff_query_runs(facts_db):
    """hardware=True 时不应引用未定义的表别名 a。"""
    intent = qa_structured.parse_intent(
        "过去一个月里有哪些硬件公司是编辑部接触过、但 Founder Park 团队还没接触过的?"
    )
    assert intent and intent["type"] == "diff"
    assert intent["hardware"] is True
    ctxs, total = qa_structured.query_diff(
        facts_db,
        intent["team_a"],
        intent["team_b"],
        intent["date_from"],
        section=intent["section"],
        hardware=True,
        date_to=intent.get("date_to"),
    )
    assert total >= 0
    assert isinstance(ctxs, list)
