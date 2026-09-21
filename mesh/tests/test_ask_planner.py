"""Planner-lite 单测。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.ask_context import empty_refs
from app.ask_planner import CONFIDENCE_THRESHOLD, plan_retrieval


@pytest.fixture(autouse=True)
def _force_rule_path(monkeypatch):
    """默认测规则兜底路径（关 LLM 意图）；LLM 路径单独测。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "0")


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


def test_e15_rule_path_stays_hybrid():
    """规则路径（LLM 关）仍不识别 e15 的改述交集——这是 LLM 层存在的理由。"""
    q = "编辑部和商业化团队两边同时跟进了哪些客户？"
    p = plan_retrieval(q)
    assert p.set_op == "none"
    assert p.path == "hybrid"


def test_e15_llm_intent_recognizes_intersect(monkeypatch):
    """LLM 意图层把「两边同时跟进」正确识别为 intersect（规则漏判）。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "编辑部和商业化团队两边同时跟进了哪些客户？"
    payload = {
        "type": "intersect",
        "team_a": "编辑部",
        "team_b": "商业化团队",
        "topic": "",
        "confidence": 0.93,
    }
    with mock.patch("app.llm.call", _mock_llm(payload)):
        p = plan_retrieval(q)
    assert p.set_op == "intersect"
    assert p.path == "structured"
    assert p.intent["team_a"] == "编辑部"
    assert p.intent["team_b"] == "商业化团队"


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


# ---- LLM 语义意图层（问法自适应）----

def _mock_llm(payload):
    def _call(system, user, **kw):
        return payload
    return _call


def test_llm_intent_paraphrased_intersect(monkeypatch):
    """换个说法「讨论过同一家公司」——正则漏，LLM 命中 intersect。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "编辑部和商业化团队有没有讨论过同一家公司？"
    # 先证明纯规则漏判
    with mock.patch.dict("os.environ", {"MESH_ASK_LLM_INTENT": "0"}):
        assert plan_retrieval(q).set_op == "none"
    # LLM 命中
    payload = {
        "type": "intersect",
        "team_a": "编辑部",
        "team_b": "商业化团队",
        "topic": "",
        "confidence": 0.9,
    }
    with mock.patch("app.llm.call", _mock_llm(payload)):
        p = plan_retrieval(q)
    assert p.set_op == "intersect"
    assert p.path == "structured"
    assert p.intent["team_a"] == "编辑部"
    assert p.intent["team_b"] == "商业化团队"


def test_llm_intent_diff_paraphrased(monkeypatch):
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "哪些公司是商业化聊过、但编辑部一直没碰的？"
    payload = {
        "type": "diff",
        "team_a": "商业化团队",
        "team_b": "编辑部",
        "confidence": 0.88,
    }
    with mock.patch("app.llm.call", _mock_llm(payload)):
        p = plan_retrieval(q)
    assert p.set_op == "diff"
    assert p.intent["team_a"] == "商业化团队"
    assert p.intent["team_b"] == "编辑部"


def test_llm_intent_unresolvable_team_falls_back(monkeypatch):
    """LLM 给了识别不出的队名 → 不采用，回落规则（不把幻觉送进 SQL）。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "火星队和月球队有没有重合的客户？"
    payload = {
        "type": "intersect",
        "team_a": "火星队",
        "team_b": "月球队",
        "confidence": 0.95,
    }
    with mock.patch("app.llm.call", _mock_llm(payload)):
        p = plan_retrieval(q)
    assert p.set_op == "none"
    assert p.path == "hybrid"


def test_llm_intent_none_does_not_break_legacy(monkeypatch):
    """LLM 判 none 时不短路，legacy 正则仍可命中。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？"
    payload = {"type": "none", "confidence": 0.4}
    with mock.patch("app.llm.call", _mock_llm(payload)):
        p = plan_retrieval(q)
    assert p.set_op == "intersect"  # 规则/legacy 兜住


def test_llm_intent_exception_falls_back(monkeypatch):
    """LLM 抛错 → 回落规则，不影响原有 diff 覆盖。"""
    monkeypatch.setenv("MESH_ASK_LLM_INTENT", "1")
    q = "商业化团队跟进了但编辑部还没接触的主体有哪些？"

    def _boom(*a, **k):
        raise RuntimeError("no api key")

    with mock.patch("app.llm.call", _boom):
        p = plan_retrieval(q)
    assert p.set_op == "diff"
    assert p.path == "structured"
