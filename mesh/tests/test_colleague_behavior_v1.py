"""Colleague Behavior v1：普通人不用学怎么跟它说话（路由 + 表达层）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import conversation as conv
from app.agent import session_state as sstore
from app.agent.feishu_cards import answer_card, followup_suggestions
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


CASES = [
    ("你好", "casual"),
    ("早", "casual"),
    ("哈哈", "casual"),
    ("谢谢", "casual"),
    ("好的", "casual"),
    ("明白", "casual"),
    ("你是谁", "help"),
    ("你能干什么", "help"),
    ("最近忙吗", "casual"),
    ("张三最近跟谁聊过", "ask_published"),
    ("那个项目呢", "clarify"),
    ("张三最近怎么样", "clarify"),
    ("我说的不是这个", "clarify"),
]


def test_colleague_behavior_routes():
    st = sstore.SessionContextState(active_entities=["小鹏"], last_topic_frame="contact")
    for q, expect in CASES:
        state = None
        if "不是" in q:
            state = st
        d = conv.route_message(q, state)
        assert d.intent == expect, (q, d.intent, d.route)
        if expect == "casual":
            assert intent_to_tool("casual") is None
            assert d.casual_text
            assert "张三" not in d.casual_text
            assert "Query Scope" not in d.casual_text
        if expect == "help":
            text = d.casual_text or ""
            assert len(text) < 120
            assert "张三最近跟谁聊过" not in text
            assert "· 「" not in text


def test_followup_lisi():
    st = sstore.SessionContextState(active_entities=["张三"], last_topic_frame="contact")
    d = conv.route_message("那李四呢", st)
    assert d.route == "followup"
    assert "李四" in d.rewritten_query


def test_meta_not_manual():
    for q in ("你是谁", "你能干什么"):
        d = conv.route_message(q)
        assert d.intent == "help"
        assert "· 「" not in (d.casual_text or "")
        assert "固定句式" in (d.casual_text or "") or "平常怎么问" in (d.casual_text or "") or "不用学" in (d.casual_text or "")


def test_no_hit_display_is_human_only():
    ans = AgentAnswer(
        text="未在已上线周报中找到与问题直接相关的记录。",
        intent="ask_published",
        context={"issue_ref": {"slug": "2026-09-08"}},
    )
    text = format_display_text(
        ans,
        payload={"n_hits": 0, "claim_support": {"support": "insufficient", "reason": "no_evidence"}},
    )
    assert "没找到" in text or "没查" in text
    assert "依据：暂无直接命中" not in text
    assert "no_evidence" not in text.lower()
    assert "期次：" not in text


def test_card_skips_followups_on_no_hit():
    body = "这期周报里我没找到能直接回答的内容。你可以换个人名/公司名，或换个说法再问一次。"
    assert followup_suggestions("张三最近跟谁聊过", display_text=body) == []
    card = answer_card(display_text=body, query="张三最近跟谁聊过")
    content = card["elements"][0]["text"]["content"]
    assert "还可以问" not in content


def test_classify_ack_not_ask():
    for q in ("你好", "早", "谢谢", "好的", "明白", "最近忙吗"):
        assert rule_classify_intent(q, _ctx(), _perm()) == "casual", q
