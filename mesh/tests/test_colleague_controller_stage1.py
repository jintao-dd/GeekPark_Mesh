"""Colleague Controller Stage 1 — tightened hard boundary + dual gate."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_controller as ctrl
from app.agent.session_state import SessionContextState


def test_hard_excludes_natural_chat_words():
    """哈哈 / 跟谁聊过 / X最近怎么样 不得靠 hard 规则定语言。"""
    assert ctrl.try_hard_path("哈哈") is None
    assert ctrl.try_hard_path("张三最近跟谁聊过") is None
    assert ctrl.try_hard_path("张三最近怎么样") is None
    assert ctrl.try_hard_path("编辑部和商务有哪些关系") is None


def test_protocol_ack_still_hard():
    d = ctrl.decide("谢谢", allow_llm=False)
    assert d.source == "hard"
    assert d.mode == "conversation"
    assert d.router_llm_used is False


def test_session_followup_still_hard():
    st = SessionContextState(
        active_entities=["张三"],
        last_topic_frame="contact",
        last_query="张三最近跟谁聊过",
    )
    d = ctrl.decide("那高德呢", st, allow_llm=False)
    assert d.source == "hard"
    assert d.mode == "followup"
    assert ctrl.will_retrieve(d)


def test_haha_goes_to_controller():
    payload = {
        "mode": "conversation",
        "intent": "casual",
        "entities": [],
        "needs_grounding": False,
        "needs_clarification": False,
        "response_mode": "conversational",
        "rewritten_query": "",
        "clarify_hint": "",
        "confidence": "high",
        "notes": "laugh",
    }
    with mock.patch("app.llm.call", return_value=payload) as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            d = ctrl.decide("哈哈", allow_llm=True)
    assert m.call_count == 1
    assert d.mode == "conversation"
    assert d.source == "llm"


def test_enterprise_goes_to_controller():
    payload = {
        "mode": "enterprise",
        "intent": "ask_published",
        "entities": ["张三"],
        "needs_grounding": True,
        "needs_clarification": False,
        "response_mode": "direct",
        "rewritten_query": "张三最近跟谁聊过",
        "clarify_hint": "",
        "confidence": "high",
        "notes": "ent",
    }
    with mock.patch("app.llm.call", return_value=payload) as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            d = ctrl.decide("张三最近跟谁聊过", allow_llm=True)
    assert m.call_count == 1
    assert d.mode == "enterprise"
    assert ctrl.will_retrieve(d)


def test_gate_a_pass():
    from eval.run_colleague_controller_stage1 import main

    assert main(["--gate", "A"]) == 0
    report = (
        Path(__file__).resolve().parents[1]
        / "eval"
        / "reports"
        / "COLLEAGUE_CONTROLLER_STAGE1_GATE_A.json"
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    s = data["summary"]
    assert s["gate_pass"] is True
    assert s["controller_decision_accuracy"] == 1.0
    assert s["mode_accuracy"] == 1.0
    assert s["needs_grounding_accuracy"] == 1.0
    assert s["hard_path_controller_llm_calls"] == 0
