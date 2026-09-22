"""2 跳图查询（cooccur / bridge）单测：用内存 DB 造共现数据。"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, qa_structured


@pytest.fixture
def graph_db(monkeypatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("MESH_DB", path)
    db.init_db(seed=False)
    con = db.connect()

    def add(item_id, name, team="编辑部", kind="company", date="2026-08-17"):
        con.execute(
            """INSERT INTO item_entity_facts
               (issue_slug, issue_id, date_start, date_end, item_id, source_id,
                owner_team, stype, entity_name, entity_kind, text_snippet, source_label, channel)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("2026-8-17", 1, "2026-08-01", date, item_id, 1,
             team, "T1", name, kind, f"{name} 相关记录", "2026-8-17 · 例会", "manual"),
        )

    # item 1: 面壁智能 + 詹杨帆 + 高通
    add(1, "面壁智能")
    add(1, "詹杨帆", kind="person")
    add(1, "高通")
    # item 2: 面壁智能 + 詹杨帆
    add(2, "面壁智能")
    add(2, "詹杨帆", kind="person")
    # item 3: 面壁智能 + 吉利银河
    add(3, "面壁智能")
    add(3, "吉利银河")
    # item 4: 吉利银河 + 高通（作为桥）
    add(4, "吉利银河")
    add(4, "高通")
    # item 5: 无关条目
    add(5, "无关公司")
    con.commit()
    yield con
    con.close()
    try:
        os.unlink(path)
    except OSError:
        pass


def test_cooccur_ranks_by_count(graph_db):
    """詹杨帆共现 2 次应排在高通/吉利银河（各 1 次）之前。"""
    ctxs, total = qa_structured.query_cooccur(graph_db, "面壁智能")
    names = [c["标题"] for c in ctxs]
    assert total == 3
    assert names[0] == "詹杨帆"
    assert set(names) == {"詹杨帆", "高通", "吉利银河"}
    # 不含种子自身
    assert "面壁智能" not in names


def test_cooccur_excludes_seed(graph_db):
    ctxs, _ = qa_structured.query_cooccur(graph_db, "面壁智能")
    assert all("面壁" not in c["标题"] for c in ctxs)


def test_cooccur_no_seed_returns_empty(graph_db):
    ctxs, total = qa_structured.query_cooccur(graph_db, "")
    assert ctxs == [] and total == 0


def test_cooccur_composite_like_match(graph_db):
    """种子是复合串的一部分（如「影眸科技」）时，LIKE 双向包含也能命中。"""
    graph_db.execute(
        """INSERT INTO item_entity_facts
           (issue_slug, issue_id, date_start, date_end, item_id, source_id,
            owner_team, stype, entity_name, entity_kind, text_snippet, source_label, channel)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-8-17", 1, "2026-08-01", "2026-08-17", 6, 1,
         "编辑部", "T1", "灵瑙科技 / 影眸科技", "company", "复合行", "例会", "manual"),
    )
    graph_db.execute(
        """INSERT INTO item_entity_facts
           (issue_slug, issue_id, date_start, date_end, item_id, source_id,
            owner_team, stype, entity_name, entity_kind, text_snippet, source_label, channel)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        ("2026-8-17", 1, "2026-08-01", "2026-08-17", 6, 1,
         "编辑部", "T1", "韩乾源", "person", "同条目", "例会", "manual"),
    )
    graph_db.commit()
    ctxs, total = qa_structured.query_cooccur(graph_db, "影眸科技")
    assert "韩乾源" in [c["标题"] for c in ctxs]


def test_bridge_finds_common(graph_db):
    """面壁智能 与 吉利银河 的桥：高通（同时共现）。"""
    ctxs, total = qa_structured.query_bridge(graph_db, "面壁智能", "吉利银河")
    names = [c["标题"] for c in ctxs]
    assert "高通" in names
    assert "面壁智能" not in names
    assert "吉利银河" not in names


def test_bridge_empty_when_no_common(graph_db):
    ctxs, total = qa_structured.query_bridge(graph_db, "面壁智能", "无关公司")
    assert total == 0


def test_bridge_missing_arg(graph_db):
    assert qa_structured.query_bridge(graph_db, "面壁智能", "") == ([], 0)


def test_run_structured_cooccur(graph_db):
    """run_structured 端到端：cooccur intent 能返回 preamble + 主体。"""
    intent = {"type": "cooccur", "seed": "面壁智能", "window_days": 120}
    out = qa_structured.run_structured(graph_db, intent)
    assert out["ok"] is True
    assert out["total"] == 3
    assert out["contexts"][0]["章节"] == "结构化检索"
    assert out["intent"]["seed"] == "面壁智能"


def test_empty_result_answer_cooccur():
    msg = qa_structured.empty_result_answer({"type": "cooccur", "seed": "某公司"})
    assert "某公司" in msg


def test_empty_result_answer_bridge():
    msg = qa_structured.empty_result_answer(
        {"type": "bridge", "seed": "A", "seed_b": "B"}
    )
    assert "A" in msg and "B" in msg