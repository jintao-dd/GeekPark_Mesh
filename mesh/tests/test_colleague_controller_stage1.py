"""Colleague Controller Stage 1 — Semantic Decision Layer tests."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_controller as ctrl
from app.agent.session_state import SessionContextState


def test_no_soft_regex_exports():
    """禁止再靠 soft phrase regex 枚举自然语言。"""
    for name in (
        "_SOFT_CHAT",
        "_SOFT_OPINION_BARE",
        "_SOFT_LOOK",
        "_SOFT_VAGUE_WORTH",
        "_CONTENT_HINT",
        "_BOUNDARY_FUZZY",
    ):
        assert not hasattr(ctrl, name), name


def test_hard_enterprise_zero_llm():
    d = ctrl.decide("张三最近跟谁聊过", allow_llm=False)
    assert d.mode == "enterprise"
    assert d.needs_grounding is True
    assert d.router_llm_used is False
    assert d.source == "hard"


def test_hard_followup_context_zero_llm():
    st = SessionContextState(
        active_entities=["张三"],
        last_topic_frame="contact",
        last_query="张三最近跟谁聊过",
    )
    d = ctrl.decide("那高德呢", st, allow_llm=False)
    assert d.mode == "followup"
    assert ctrl.will_retrieve(d)
    assert "高德" in d.rewritten_query
    assert d.router_llm_used is False


def test_natural_language_goes_semantic_not_regex():
    """未见说法：不在 hard path，应走 Controller LLM。"""
    payload = {
        "mode": "conversation",
        "intent": "casual",
        "entities": [],
        "topic": "",
        "needs_grounding": False,
        "needs_clarification": False,
        "response_mode": "conversational",
        "rewritten_query": "",
        "clarify_hint": "",
        "confidence": "high",
        "notes": "vent",
    }
    with mock.patch("app.llm.call", return_value=payload) as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            d = ctrl.decide("今天忙死了", allow_llm=True)
    assert m.call_count == 1
    assert m.call_args.kwargs.get("task") == "controller"
    assert d.mode == "conversation"
    assert not ctrl.will_retrieve(d)
    assert d.router_llm_used is True


def test_unseen_opinion_no_blind_retrieve():
    payload = {
        "mode": "clarify",
        "intent": "clarify",
        "entities": [],
        "needs_grounding": False,
        "needs_clarification": True,
        "response_mode": "clarify",
        "rewritten_query": "",
        "clarify_hint": "你指哪家公司？",
        "confidence": "medium",
        "notes": "vague",
    }
    with mock.patch("app.llm.call", return_value=payload):
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            d = ctrl.decide("你觉得这个靠谱不", allow_llm=True)
    assert d.mode == "clarify"
    assert not ctrl.will_retrieve(d)


def test_semantic_uses_session_block():
    st = SessionContextState(
        active_entities=["刘先明"],
        last_topic_frame="contact",
        last_query="刘先明最近跟谁聊过",
        recent_turns=[{"role": "user", "text": "刘先明最近跟谁聊过", "route": "ask"}],
    )
    payload = {
        "mode": "followup",
        "intent": "ask_published",
        "entities": ["刘先明"],
        "topic": "business_contact",
        "needs_grounding": True,
        "needs_clarification": False,
        "response_mode": "followup",
        "rewritten_query": "刘先明还有其他相关记录或接触对象吗",
        "clarify_hint": "",
        "confidence": "high",
        "notes": "more",
    }
    with mock.patch("app.llm.call", return_value=payload) as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            # 未见结构：不走 hard followup regex
            d = ctrl.decide("顺着刚才那个人，还有没有别人", st, allow_llm=True)
    assert m.call_count == 1
    user_prompt = m.call_args.args[1] if len(m.call_args.args) > 1 else m.call_args.kwargs.get("user")
    # llm.call(system, user, ...)
    assert "刘先明" in (m.call_args.args[1] or "")
    assert d.mode == "followup"
    assert ctrl.will_retrieve(d)


def test_fallback_never_blinds_retrieve():
    d = ctrl.decide("某种从未见过的含糊表达xyz", allow_llm=False)
    assert d.source == "fallback"
    assert not ctrl.will_retrieve(d)


def test_stage1_gate_script():
    from eval.run_colleague_controller_stage1 import main

    assert main() == 0
    report = (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "reports"
        / "COLLEAGUE_CONTROLLER_STAGE1.json"
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["gate_pass"] is True
    assert data["summary"]["retrieval_when_unneeded"] == 0
    assert data["summary"]["missed_grounding"] == 0
    assert data["summary"]["hard_path_controller_llm_calls"] == 0
