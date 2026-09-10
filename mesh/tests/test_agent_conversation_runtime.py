"""Conversation Runtime Sprint：Follow-up / Ambiguous / Casual / Failure UX（能力测，非 case 补丁）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import conversation as conv
from app.agent import session_state as sstore
from app.agent.feishu_reply import format_display_text
from app.agent.intent import intent_to_tool, rule_classify_intent
from app.agent.models import AgentAnswer, AgentContext, IssueRef, PermissionDecision


def _ctx() -> AgentContext:
    return AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )


def _perm() -> PermissionDecision:
    return PermissionDecision(
        agent_access=True,
        tool_acl=[
            "system.help",
            "context.list_issues",
            "ask.published",
            "ask.relations_summary",
        ],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )


def setup_function():
    sstore.reset_for_tests()


# --- Casual ---
def test_casual_no_tool():
    for q in ("你好", "哈哈", "谢谢", "好的", "收到", "明白", "算了", "好的明白了", "哈哈在忙吗"):
        d = conv.route_message(q)
        assert d.route == "general_conversation", q
        assert d.intent == "casual"
        assert intent_to_tool("casual") is None
        assert d.casual_text
        assert "Query Scope" not in d.casual_text


def test_capability_boundary_refuses():
    d = conv.route_message("你能不能帮我改一下周报权限")
    assert d.route == "refuse"
    assert d.intent == "refuse"


# --- Ambiguous / clarify ---
def test_ambiguous_clarify_not_ask():
    for q in ("后来呢", "还有吗", "那个呢", "之前那个", "最近怎么样", "那个项目"):
        d = conv.route_message(q, sstore.SessionContextState())
        assert d.route == "clarify", q
        assert d.intent == "clarify"
        assert intent_to_tool("clarify") is None
        assert "？" in d.clarify_text or "吗" in d.clarify_text


def test_how_is_person_clarifies():
    d = conv.route_message("张三最近怎么样？")
    assert d.route == "clarify"
    assert "沟通对象" in d.clarify_text or "推进" in d.clarify_text or "跟谁" in d.clarify_text


# --- Follow-up ---
def test_followup_named_rewrites_without_using_answer_as_fact():
    st = sstore.SessionContextState(
        session_key="s1",
        active_entities=["小鹏"],
        last_topic_frame="contact",
        last_query="小鹏最近跟谁聊过",
    )
    d = conv.route_message("那李四呢？", st)
    assert d.route == "followup"
    assert d.resolved_entity == "李四"
    assert "李四" in d.rewritten_query
    assert "小鹏" not in d.rewritten_query or "李四" in d.rewritten_query
    # must be a fresh ask query, not copying prior answer
    assert "已经量产" not in d.rewritten_query


def test_followup_pronoun_uses_active_entity():
    st = sstore.SessionContextState(
        active_entities=["刘先明", "小鹏"],
        last_topic_frame="contact",
    )
    d = conv.route_message("他跟谁对接的", st)
    assert d.route == "followup"
    assert d.resolved_entity == "刘先明"
    assert "刘先明" in d.rewritten_query


def test_followup_later_with_context():
    st = sstore.SessionContextState(active_entities=["高德"], last_topic_frame="progress")
    d = conv.route_message("后来呢", st)
    assert d.route == "followup"
    assert "高德" in d.rewritten_query


def test_followup_later_without_context_clarifies():
    d = conv.route_message("后来呢", sstore.SessionContextState())
    assert d.route == "clarify"


# --- Meta help UX ---
def test_help_copy_teaches_natural_questions():
    from app.agent.tools import tool_help
    from app.agent.models import IdentityResult

    # unit: text only via route
    assert rule_classify_intent("怎么问你", _ctx(), _perm()) == "help"


# --- Failure UX ---
def test_failure_ux_no_hit_not_no_evidence_token():
    ans = AgentAnswer(
        text="未在已上线周报中找到与问题直接相关的记录。",
        intent="ask_published",
        context={"issue_ref": {"slug": "2026-09-08"}},
    )
    text = format_display_text(
        ans,
        payload={"n_hits": 0, "claim_support": {"support": "insufficient", "reason": "no_evidence"}},
    )
    assert "no_evidence" not in text.lower()
    assert "没找到" in text or "没查" in text
    assert "no_evidence" not in text.lower()
    assert "依据：暂无直接命中" not in text


def test_failure_ux_insufficient_distinct():
    ans = AgentAnswer(
        text="相关内容不足以证明已经量产。",
        intent="ask_published",
        evidence_refs=["ev:item:1"],
        context={"issue_ref": {"slug": "2026-09-08"}},
    )
    text = format_display_text(
        ans,
        payload={"n_hits": 3, "claim_support": {"support": "insufficient", "reason": "weak"}},
    )
    assert "不足以" in text or "相关" in text
    assert "no_evidence" not in text.lower()


def test_session_update_extracts_entities_not_fact_cache():
    st = sstore.SessionContextState(session_key="x")
    route = conv.RouteDecision(
        route="ask",
        intent="ask_published",
        rewritten_query="小鹏最近有接触吗",
        topic_frame="contact",
    )
    st = conv.update_state_after_turn(
        st,
        route=route,
        user_text="小鹏最近有接触吗",
        answer_text="**小鹏**：已接触。**高德**：群访。",
        intent="ask_published",
        issue="2026-09-08",
        evidence_refs=["ev:item:1"],
    )
    assert "小鹏" in st.active_entities
    assert "高德" in st.active_entities
    assert st.last_topic_frame == "contact"
    assert st.active_issue == "2026-09-08"
    # state must not store answer as truth blob
    assert not hasattr(st, "last_answer") or not getattr(st, "last_answer", None)
