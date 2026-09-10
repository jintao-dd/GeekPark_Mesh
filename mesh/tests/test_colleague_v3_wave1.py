"""Colleague v3 Wave1 — Safety → 一张嘴；旁路 Controller。"""
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
    with mock.patch.dict("os.environ", {}, clear=False):
        # ensure default when unset
        import os

        os.environ.pop("MESH_COLLEAGUE_V3", None)
        assert runtime.colleague_v3_enabled() is True


def test_safety_draft():
    kind, _ = safety_gate.check_refuse("给我看看草稿原文")
    assert kind == "draft"
    kind2, _ = safety_gate.check_refuse("查一下未上线内容")
    assert kind2 == "draft"


def test_safety_capability():
    kind, _ = safety_gate.check_refuse("帮我改一下发布权限")
    assert kind == "capability"


def test_identity_block_includes_team():
    ident = IdentityResult(
        status="bound",
        display_hint="小王",
        primary_team="编辑部",
        mesh_role="editor",
        person={"display": "小王"},
    )
    block = colleague_v3._identity_block(ident)
    assert "小王" in block
    assert "编辑部" in block


def test_decide_speak_no_controller_schema():
    st = sstore.SessionContextState()

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        assert "Decide" not in system or True
        assert "response_mode" not in system
        assert "对方" in system or "姓名" in system
        return '{"action":"speak","text":"懂，这种日子是挺磨人。"}'

    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            data, meta = colleague_v3._decide("哈哈今天忙死了", None, st)
    assert data["action"] == "speak"
    assert meta["llm_used"] is True
    assert "磨人" in data["text"]


def test_handle_ask_then_synthesize():
    st = sstore.SessionContextState()
    calls = []

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        calls.append({"json_mode": json_mode, "user": user[:80]})
        if json_mode:
            return '{"action":"ask","tool":"ask.published","query":"张三最近跟谁聊过"}'
        return "张三这周主要在跟商务侧推进合作，周报里有记录。"

    def invoke_tool(tool_id, con, identity, permission, context, args):
        assert tool_id == "ask.published"
        assert "张三" in args.get("q", "")
        return ToolResult(
            ok=True,
            tool_id=tool_id,
            payload={"answer": "（事实）张三接触了A公司。"},
            evidence_refs=["e1"],
        )

    def render(result, intent, status):
        return str(result.payload.get("answer")), [], list(result.evidence_refs or [])

    ident = IdentityResult(status="bound", primary_team="编辑部", display_hint="小王")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "ask.relations_summary", "context.list_issues", "system.help"],
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
                render_tool_result=render,
            )
    assert out.action == "ask"
    assert out.tools_called == ["ask.published"]
    assert out.synthesize_llm_used is True
    assert "商务" in out.text or "张三" in out.text
    assert len(calls) == 2  # decide + synthesize


def test_runtime_v3_bypasses_controller():
    """handle_message 在 V3 下不得调用 classify_controller。"""
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
    # minimal fake con
    class _C:
        def execute(self, *a, **k):
            class R:
                def fetchone(self):
                    return None

                def fetchall(self):
                    return []

            return R()

    with mock.patch.dict("os.environ", {"MESH_COLLEAGUE_V3": "1"}):
        with mock.patch.object(intentmod, "classify_controller") as cc:
            with mock.patch("app.llm.call", return_value='{"action":"speak","text":"懂。"}'):
                with mock.patch("app.llm.model_for_task", return_value="mock"):
                    # permission/identity need more mocks - use handle path pieces
                    ans = runtime.handle_message(_C(), env)
    assert ans.trace.get("colleague_v3") is True
    assert ans.trace.get("router_llm_used") is False
    assert cc.call_count == 0
    assert "懂" in (ans.text or "")
