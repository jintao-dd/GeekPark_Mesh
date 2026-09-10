"""Stage 2A · Conversation Core — unit / mock gate（不扩 Gold）。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_chat
from app.agent import colleague_core as ccore
from app.agent import session_state as sstore


def setup_function():
    sstore.reset_for_tests()


def test_no_legacy_short_guardrails_in_system():
    assert "1～3 句" not in colleague_chat._SYSTEM
    assert "1-3" not in colleague_chat._SYSTEM


def test_response_mode_budgets():
    assert ccore.RESPONSE_BUDGETS["direct"][0] < ccore.RESPONSE_BUDGETS["conversational"][0]
    assert ccore.RESPONSE_BUDGETS["opinion"][0] > 350
    assert ccore.RESPONSE_BUDGETS["conversational"][1] > 600
    assert ccore.RESPONSE_BUDGETS["rewrite"][0] >= 800


def test_feedback_updates_session_relationship():
    st = sstore.SessionContextState()
    ccore.apply_user_signal(st, "你刚才有点机械，我想让你成为我们的同事")
    assert st.colleague_relationship == "familiar-colleague"
    assert "同事" in (st.colleague_pref or "")
    assert st.colleague_feedback
    assert st.colleague_user_tone in ("corrective", "frustrated", "casual")


def test_tone_busy_day():
    st = sstore.SessionContextState()
    ccore.apply_user_signal(st, "哈哈今天忙死了")
    assert st.colleague_emotional_tone in ("casual", "excited")


def test_reply_uses_budget_and_passes_response_mode():
    st = sstore.SessionContextState()
    captured = {}

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        captured["system"] = system
        captured["max_tokens"] = max_tokens
        captured["task"] = task
        return "我觉得这事可以先缓一缓，别急着上强度。"

    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock-answer"):
            text, meta = colleague_chat.reply_colleague(
                "你觉得这个怎么样",
                st,
                response_mode="opinion",
            )
    assert "缓一缓" in text
    assert meta["llm_used"] is True
    assert meta["response_mode"] == "opinion"
    assert meta["max_tokens"] == ccore.RESPONSE_BUDGETS["opinion"][0]
    assert captured["max_tokens"] == ccore.RESPONSE_BUDGETS["opinion"][0]
    assert "Colleague Core" in captured["system"]
    assert "OPINION" in captured["system"]


def test_soft_cap_not_legacy_600_for_conversational():
    _, soft, _ = ccore.RESPONSE_BUDGETS["conversational"]
    assert soft > 600


def test_correction_chain_pref_persists_in_core():
    st = sstore.SessionContextState()
    ccore.apply_user_signal(st, "你和傻子没区别。")
    ccore.apply_user_signal(st, "你自己感觉呢？")
    ccore.apply_user_signal(st, "我想让你成为我们同事。")
    core = ccore.build_core(st, response_mode="conversational")
    assert core.relationship_context == "familiar-colleague"
    assert core.user_feedback
    block = ccore.render_context_block(core)
    assert "用户近期反馈" in block


def test_fallback_no_customer_service():
    t = colleague_chat._fallback("你太机械了", mode="chat", response_mode="conversational")
    assert "抱歉" not in t
    assert "帮您" not in t
