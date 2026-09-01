"""Planner-lite 单测。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ask_context import empty_refs
from app.ask_planner import CONFIDENCE_THRESHOLD, plan_retrieval


def test_e08_diff_set_op():
    q = "商业化团队跟进了但编辑部还没接触的主体有哪些？"
    p = plan_retrieval(q)
    assert p.set_op == "diff"
    assert p.path == "structured"
    assert p.confidence >= CONFIDENCE_THRESHOLD
    assert p.intent and p.intent["team_a"] == "商业化团队"
    assert p.intent["team_b"] == "编辑部"


def test_e10_by_team_hardware():
    q = "各团队最近关注了哪些硬件相关话题？"
    p = plan_retrieval(q)
    assert p.set_op == "by_team"
    assert p.path == "structured"
    assert p.hardware is True
    assert p.intent and p.intent.get("topic") == "硬件"


def test_e21_intersect_overlap():
    q = "编辑部和商业化团队在可同步关系上有哪些重叠？"
    p = plan_retrieval(q)
    assert p.set_op == "intersect"
    assert p.path == "structured"
    assert p.intent and p.intent.get("section") == "关系"


def test_e09_overseas_legacy_path():
    q = "海外团队有接触、国内团队还没跟进的公司有哪些？"
    p = plan_retrieval(q)
    assert p.set_op == "overseas_gap"
    assert p.path == "structured"


def test_e01_intersect_still_works():
    q = "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？"
    p = plan_retrieval(q)
    assert p.set_op == "intersect"
    assert p.path == "structured"


def test_e15_stays_hybrid_not_intersect():
    q = "编辑部和商业化团队两边同时跟进了哪些客户？"
    p = plan_retrieval(q)
    assert p.set_op == "none"
    assert p.path == "hybrid"


def test_followup_skips_planner_structured():
    refs = {
        **empty_refs(),
        "last_user_q": "具身智能有哪些公司",
        "chunk_ids": ["c1"],
        "entities": ["优必选"],
    }
    p = plan_retrieval("还有哪些", refs)
    assert p.path == "hybrid"
    assert p.set_op == "none"
    assert p.confidence == 0.0


def test_low_confidence_by_team_fallback():
    q = "关于AI各团队分别知道什么"
    p = plan_retrieval(q)
    assert p.set_op == "by_team"
    assert p.path == "hybrid"
    assert p.confidence < CONFIDENCE_THRESHOLD
    assert "未做集合运算" in p.fallback_note


def test_hybrid_topic_unchanged():
    q = "面壁智能"
    p = plan_retrieval(q)
    assert p.set_op == "none"
    assert p.path == "hybrid"


def test_prepare_e08_structured_on_golden():
    import json
    import tempfile
    from pathlib import Path

    from app import ask_engine, db, db_conn
    from app.ask_scope import AskScope
    from eval.run_acceptance import _seed_golden_db

    td = tempfile.mkdtemp()
    db_conn.DB_PATH = str(Path(td) / "p.db")
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = db_conn.DB_PATH
    con = db.connect()
    db.init_db(seed=False)
    _seed_golden_db(con)
    scope = AskScope(channel="web", user_id=1, role="viewer")
    prep = ask_engine.prepare(
        con,
        "商业化团队跟进了但编辑部还没接触的主体有哪些？",
        scope,
    )
    assert prep.get("mode") == "structured"
    assert (prep.get("retrieval_plan") or {}).get("set_op") == "diff"
    con.close()
