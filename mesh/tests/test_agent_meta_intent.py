"""Agent P0.0：meta / identity 问句不得进入 Retrieval。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.intent import intent_to_tool, normalize_query, rule_classify_intent
from app.agent.models import (
    AgentContext,
    IssueRef,
    PermissionDecision,
)


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


def test_normalize_strips_feishu_mention():
    assert normalize_query("@GEEKPARK Mesh 你是谁") == "你是谁"
    assert normalize_query("@_user_1 我是谁？") == "我是谁？"


def test_meta_queries_are_help_not_ask():
    cases = [
        "你是谁",
        "你是?",
        "你是？",
        "@Bot 你是什么",
        "你能干什么",
        "你能做什么",
        "帮助",
        "怎么问你",
        "who are you",
    ]
    for q in cases:
        intent = rule_classify_intent(q, _ctx(), _perm())
        assert intent == "help", q
        assert intent_to_tool(intent) == "system.help"
        assert intent_to_tool(intent) not in (
            "ask.published",
            "ask.relations_summary",
        )


def test_greeting_is_casual_not_ask():
    for q in ("你好", "hi", "谢谢", "好的", "收到"):
        intent = rule_classify_intent(q, _ctx(), _perm())
        assert intent == "casual", q
        assert intent_to_tool(intent) is None


def test_whoami_short_circuits():
    for q in ("我是谁", "我是谁？", "@Mesh 我是谁", "who am i"):
        intent = rule_classify_intent(q, _ctx(), _perm())
        assert intent == "whoami", q
        assert intent_to_tool(intent) is None


def test_fact_and_relation_unaffected():
    assert rule_classify_intent("编辑部关注了哪些公司", _ctx(), _perm()) == "ask_published"
    assert rule_classify_intent("本期有哪些可同步的关系", _ctx(), _perm()) == "ask_relations"
    assert intent_to_tool("ask_published") == "ask.published"
    assert intent_to_tool("ask_relations") == "ask.relations_summary"
