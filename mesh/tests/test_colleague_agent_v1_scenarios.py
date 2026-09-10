"""Colleague Agent v1 · Conversation Scenarios（不扩 Gold）。

覆盖：事实 follow-up、话题切换回切、模糊澄清、用户纠正、观点讨论、聊天→事实、session 隔离。
不测 Ranking / Claim / Retrieval 质量。
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import conversation as conv
from app.agent import colleague_chat
from app.agent import session_state as sstore


def setup_function():
    sstore.reset_for_tests()


def _apply(st: sstore.SessionContextState, q: str, answer: str = "") -> conv.RouteDecision:
    d = conv.route_message(q, st)
    conv.update_state_after_turn(
        st,
        route=d,
        user_text=q,
        answer_text=answer,
        intent=d.intent,
        issue="2026-09-08" if d.intent.startswith("ask") or d.route == "followup" else "",
        evidence_refs=["ev:item:1"] if d.route in ("ask", "followup", "relations") else None,
    )
    return d


# 1) 事实 → follow-up → follow-up
def test_scenario_fact_followup_chain():
    st = sstore.SessionContextState(session_key="s1")
    d1 = _apply(st, "张三最近跟谁聊过", answer="**张三** 接触了 **李四**。")
    assert d1.route == "ask"
    assert "张三" in st.active_entities

    d2 = _apply(st, "那李四呢")
    assert d2.route == "followup"
    assert d2.resolved_entity == "李四"
    assert "李四" in d2.rewritten_query

    d3 = _apply(st, "后来呢")
    assert d3.route == "followup"
    assert "李四" in d3.rewritten_query or "张三" in d3.rewritten_query


# 2) 事实 → topic switch → 回原 topic
def test_scenario_topic_switch_and_resume():
    st = sstore.SessionContextState(session_key="s2")
    _apply(st, "张三最近跟谁聊过", answer="**张三** 接触了高德相关团队。")
    assert st.conversation_mode == "enterprise"
    assert "张三" in st.active_entities

    d_chat = _apply(st, "哈哈今天好忙")
    assert d_chat.route == "general_conversation"
    assert st.conversation_mode == "chat"
    assert st.topic_stack, "enterprise topic should be pushed"

    d_opin = _apply(st, "你觉得最近 AI 怎么样")
    assert d_opin.route == "general_conversation"
    assert d_opin.intent == "casual"

    d_back = _apply(st, "对了，高德呢")
    assert d_back.route == "followup"
    assert d_back.resolved_entity == "高德"
    assert "高德" in d_back.rewritten_query
    assert st.conversation_mode == "enterprise"


# 3) 模糊 → 澄清（不盲目 Retrieval）
def test_scenario_ambiguous_clarify():
    d = conv.route_message("张三最近怎么样")
    assert d.route == "clarify"
    assert conv.intent_to_tool(d.intent) is None
    assert "跟谁" in d.clarify_text or "推进" in d.clarify_text


# 4) 用户纠正 → Agent 修正姿态（澄清，不硬查）
def test_scenario_user_correction_repair():
    st = sstore.SessionContextState(
        session_key="s4",
        active_entities=["张三"],
        last_topic_frame="contact",
        conversation_mode="enterprise",
    )
    d = _apply(st, "我说的不是这个")
    assert d.route == "clarify"
    assert d.notes == "repair"
    assert conv.intent_to_tool("clarify") is None


# 5) 企业事实 → 普通观点讨论（不进 Ask）
def test_scenario_enterprise_then_opinion():
    st = sstore.SessionContextState(session_key="s5")
    _apply(st, "高德最近跟谁聊过", answer="有接触记录。")
    d = _apply(st, "你怎么看这个方案")
    assert d.route == "general_conversation"
    assert conv.intent_to_tool(d.intent) is None


# 6) 普通聊天 → 企业事实
def test_scenario_chat_then_enterprise():
    st = sstore.SessionContextState(session_key="s6")
    d0 = _apply(st, "你好")
    assert d0.route == "general_conversation"
    d1 = _apply(st, "张三最近跟谁聊过")
    assert d1.route == "ask"
    assert d1.intent == "ask_published"


# 7) 多用户 / 多 session 隔离
def test_scenario_session_isolation():
    a = sstore.SessionContextState(session_key="dm:userA")
    b = sstore.SessionContextState(session_key="dm:userB")
    _apply(a, "张三最近跟谁聊过", answer="**张三**。")
    _apply(b, "李四最近跟谁聊过", answer="**李四**。")
    sstore.save(a)
    sstore.save(b)

    la = sstore.load("dm:userA")
    lb = sstore.load("dm:userB")
    assert "张三" in la.active_entities
    assert "李四" not in la.active_entities
    assert "李四" in lb.active_entities
    assert "张三" not in lb.active_entities

    da = conv.route_message("他后来呢", la)
    db = conv.route_message("他后来呢", lb)
    assert da.resolved_entity == "张三"
    assert db.resolved_entity == "李四"


def test_colleague_chat_calls_answer_llm_once():
    st = sstore.SessionContextState(recent_turns=[{"role": "user", "text": "早", "route": "general_conversation"}])
    with mock.patch("app.llm.call", return_value="还行，有事直接说。") as m:
        with mock.patch("app.llm.model_for_task", return_value="mock-answer"):
            text, meta = colleague_chat.reply_colleague("今天好忙", st, mode="chat")
    assert m.call_count == 1
    assert meta.get("llm_used") is True
    assert "忙" in text or "事" in text
    # no retrieval side effects
    assert m.call_args.kwargs.get("task") == "answer" or (
        len(m.call_args.args) >= 0 and m.call_args.kwargs.get("json_mode") is False
    )


def test_memory_not_fact_source_followup_rewrites():
    """Session 只消解指代；rewrite 后仍走 Ask（本测只断言 rewrite，不缓存答案事实）。"""
    st = sstore.SessionContextState(
        active_entities=["小鹏"],
        last_topic_frame="contact",
        last_query="小鹏最近跟谁聊过",
        conversation_mode="enterprise",
    )
    # 故意在 recent_turns 塞假事实
    st.append_turn(role="assistant", text="小鹏已经量产一千万台", route="ask")
    d = conv.route_message("那高德呢", st)
    assert d.route == "followup"
    assert "高德" in d.rewritten_query
    assert "一千万" not in d.rewritten_query
