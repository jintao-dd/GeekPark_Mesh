"""Colleague v3 Wave1 — 协议不泄漏；speak 明文成文。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_v3
from app.agent import runtime
from app.agent import safety_gate
from app.agent import session_state as sstore
from app.agent.models import (
    AgentContext,
    AgentEnvelope,
    IdentityResult,
    IssueRef,
    PermissionDecision,
    ToolResult,
)


def setup_function():
    sstore.reset_for_tests()


def test_v3_enabled_by_default():
    import os

    os.environ.pop("MESH_COLLEAGUE_V3", None)
    assert runtime.colleague_v3_enabled() is True


def test_safety_draft():
    kind, _ = safety_gate.check_refuse("给我看看草稿原文")
    assert kind == "draft"


def test_safety_capability():
    kind, _ = safety_gate.check_refuse("帮我改一下发布权限")
    assert kind == "capability"


def test_parse_single_quoted_python_dict():
    raw = "{'action': 'speak', 'text': '哈哈确实有点傻'}"
    d = colleague_v3._parse_decision(raw)
    assert d["action"] == "speak"
    # decide 不再依赖 text 字段；即便有也不应整段回传
    assert d.get("action") == "speak"


def test_sanitize_strips_leaked_protocol():
    leaked = "{'action': 'speak', 'text': '懂，这种日子挺磨人。'}"
    out = colleague_v3._sanitize_user_visible(leaked)
    assert "action" not in out
    assert "磨人" in out


def test_sanitize_never_returns_raw_json_blob():
    leaked = '{"action":"speak","text":"hello"}'
    out = colleague_v3._sanitize_user_visible(leaked)
    assert out == "hello" or "action" not in out


def test_handle_speak_uses_plain_second_call():
    st = sstore.SessionContextState()
    calls = []

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        calls.append({"json_mode": json_mode, "max_tokens": max_tokens})
        if json_mode:
            return '{"action":"speak"}'
        return "懂，这种日子是挺磨人。有想对的人和事直接丢过来。"

    ident = IdentityResult(status="bound", primary_team="编辑部", display_hint="小王")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "ask.relations_summary", "context.list_issues"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )
    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            out = colleague_v3.handle(
                con=None,
                user_text="哈哈今天忙死了",
                identity=ident,
                permission=perm,
                context=ctx,
                session=st,
                invoke_tool=lambda *a, **k: None,
                render_tool_result=lambda *a, **k: ("", [], []),
            )
    assert out.action == "speak"
    assert "action" not in out.text
    assert "磨人" in out.text
    assert len(calls) == 2
    assert calls[0]["json_mode"] is True
    assert calls[1]["json_mode"] is False
    assert calls[1]["max_tokens"] >= 3000


def test_handle_ask_then_synthesize():
    st = sstore.SessionContextState()

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            if "Task Planner" in (system or ""):
                return {
                    "band": "ordinary",
                    "goal": "张三最近跟谁聊过",
                    "steps": [
                        {
                            "id": "s1",
                            "tool": "ask.published",
                            "args": {"query": "张三最近跟谁聊过"},
                        }
                    ],
                }
            return '{"action":"work","tool":"ask.published","query":"张三最近跟谁聊过"}'
        return "张三这周主要在跟商务侧推进合作。"

    def invoke_tool(tool_id, con, identity, permission, context, args):
        return ToolResult(
            ok=True,
            tool_id=tool_id,
            payload={"answer": "（事实）张三接触了A公司。"},
            evidence_refs=["e1"],
        )

    ident = IdentityResult(status="bound", primary_team="编辑部", display_hint="小王")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "ask.relations_summary", "context.list_issues"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )
    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            out = colleague_v3.handle(
                con=None,
                user_text="张三最近跟谁聊过",
                identity=ident,
                permission=perm,
                context=ctx,
                session=st,
                invoke_tool=invoke_tool,
                render_tool_result=lambda r, i, s: (
                    str(r.payload.get("answer")),
                    [],
                    list(r.evidence_refs or []),
                ),
            )
    assert out.action == "ask"
    assert "action" not in out.text
    assert "张三" in out.text or "商务" in out.text or "A公司" in out.text
    assert out.tools_called  # work → Planner → 工具


def test_runtime_v3_bypasses_controller():
    from app.agent import intent as intentmod

    env = AgentEnvelope(
        text="哈哈今天忙死了",
        channel="harness",
        feishu_open_id="ou_test",
        identity_override={
            "status": "bound",
            "mesh_user_id": 1,
            "display_hint": "小王",
            "primary_team": "编辑部",
            "mesh_role": "editor",
            "person": {"display": "小王"},
        },
    )

    class _C:
        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return None

                def fetchall(self):
                    return []

            return R()

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return '{"action":"speak"}'
        return "懂。"

    with mock.patch.dict("os.environ", {"MESH_COLLEAGUE_V3": "1"}):
        with mock.patch.object(intentmod, "classify_controller") as cc:
            with mock.patch("app.llm.call", side_effect=fake_call):
                with mock.patch("app.llm.model_for_task", return_value="mock"):
                    ans = runtime.handle_message(_C(), env)
    assert ans.trace.get("colleague_v3") is True
    assert cc.call_count == 0
    assert "action" not in (ans.text or "")
    assert "懂" in (ans.text or "")
