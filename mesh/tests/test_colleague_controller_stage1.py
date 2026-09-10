"""Colleague Controller Stage 1 unit + gate scenarios."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_controller as ctrl
from app.agent import intent as intentmod
from app.agent.session_state import SessionContextState


def test_unified_schema_fields():
    d = ctrl.decide("张三最近跟谁聊过", allow_llm=False)
    assert d.mode == "enterprise"
    assert d.needs_grounding is True
    assert d.needs_clarification is False
    assert d.response_mode == "direct"
    assert isinstance(d.entities, list)
    assert isinstance(d.context_refs, list)
    assert d.router_llm_used is False


def test_chat_no_retrieve():
    for q in ("哈哈", "谢谢", "今天事情好多", "你怎么看"):
        d = ctrl.decide(q, allow_llm=False)
        assert d.mode in ("conversation", "content"), q
        assert not ctrl.will_retrieve(d), q
        assert d.router_llm_used is False


def test_enterprise_retrieve():
    d = ctrl.decide("张三最近跟谁聊过", allow_llm=False)
    assert d.mode == "enterprise"
    assert ctrl.will_retrieve(d)


def test_followup_uses_context():
    st = SessionContextState(active_entities=["张三"], last_topic_frame="contact")
    d = ctrl.decide("那高德呢", st, allow_llm=False)
    assert d.mode == "followup"
    assert ctrl.will_retrieve(d)
    assert "高德" in d.rewritten_query
    assert d.router_llm_used is False


def test_clarify_no_retrieve():
    for q in ("最近怎么样", "张三最近怎么样", "这个靠谱吗", "那个呢"):
        d = ctrl.decide(q, allow_llm=False)
        assert d.mode == "clarify", (q, d.mode)
        assert d.needs_clarification
        assert not ctrl.will_retrieve(d)


def test_meta_no_retrieve():
    d = ctrl.decide("你是谁", allow_llm=False)
    assert d.mode == "meta"
    assert not ctrl.will_retrieve(d)


def test_controller_llm_once_on_low_conf():
    payload = {
        "mode": "clarify",
        "intent": "clarify",
        "entities": [],
        "topic": "",
        "needs_grounding": False,
        "needs_clarification": True,
        "response_mode": "clarify",
        "rewritten_query": "",
        "clarify_hint": "你具体指谁？",
        "notes": "vague",
    }
    # 低置信、无强 grounding、不进 soft/fast 高置信：应 1× Controller LLM
    q = "帮我判断一下上周提到的那个方向现在处在什么阶段"
    with mock.patch("app.llm.call", return_value=payload) as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-c"):
            d = ctrl.decide(q, allow_llm=True)
    assert m.call_count == 1
    assert m.call_args.kwargs.get("task") == "controller"
    assert d.router_llm_used is True
    assert d.mode == "clarify"
    assert not ctrl.will_retrieve(d)


def test_intent_classify_uses_controller():
    r = intentmod.classify_route("哈哈")
    assert r.route == "general_conversation"
    c = intentmod.classify_controller("张三最近跟谁聊过")
    assert c.mode == "enterprise"


def test_stage1_gate_script():
    from eval.run_colleague_controller_stage1 import main

    assert main() == 0
    report = Path(__file__).resolve().parents[1] / "eval" / "reports" / "COLLEAGUE_CONTROLLER_STAGE1.json"
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["summary"]["gate_pass"] is True
    assert data["summary"]["retrieval_when_unneeded"] == 0
    assert data["summary"]["missed_grounding"] == 0
