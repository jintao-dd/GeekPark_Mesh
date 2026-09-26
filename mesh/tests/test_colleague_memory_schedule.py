"""短期工作记忆 + 隔离调度 + 假 Hands 声称拦截。"""
from __future__ import annotations

import os
from unittest import mock

from app.agent import colleague_v3, tools as toolsmod
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
    ToolResult,
)
from app.agent.session_state import SessionContextState
from app.agent.tool_contract import FEISHU_ALL_TOOLS


def _ident():
    return IdentityResult(status="bound", primary_team="编辑部", display_hint="测试")


def _perm_all():
    return PermissionDecision(
        agent_access=True,
        tool_acl=list(FEISHU_ALL_TOOLS)
        + ["ask.published", "ask.relations_summary", "context.list_issues"],
        data_visibility={"forbid": ["draft", "raw", "unpublished"]},
        query_scope={"mode": "primary", "team_focus": "编辑部"},
        published_only=True,
    )


def _ctx():
    return AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
        chat_id="oc_test",
    )


def test_classify_scope_denied():
    c = colleague_v3._classify_block(
        "feishu_api_99991672: Access denied. docx:document:create",
        tool="feishu.doc.create",
    )
    assert c["code"] == "scope_denied"


def test_strip_fake_hands_claims():
    bad = "正在创建——（调用 feishu 文档写入中）"
    out = colleague_v3._strip_fake_hands_claims(bad)
    assert "没有真正写入" in out
    assert "正在创建" not in out


def test_working_memory_block_includes_goal_and_block():
    st = SessionContextState()
    st.active_goal = {
        "summary": "创建自我介绍文档",
        "family": "feishu_write",
        "tool": "feishu.doc.create",
        "utterance": "创建文档介绍你自己",
    }
    st.last_block = {"code": "scope_denied", "tool": "feishu.doc.create", "message": "no scope"}
    block = colleague_v3._working_memory_block(st)
    assert "未完成目标" in block
    assert "scope_denied" in block


def test_prepare_sets_active_goal_confirm_clears():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    st = SessionContextState()

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            if "确认" in user.split("用户：")[-1][:20]:
                return '{"situation":"confirm_pending","action":"confirm_write"}'
            return (
                '{"situation":"want_feishu_write","action":"prepare_write",'
                '"tool":"feishu.doc.create","args":{"title":"Mesh","content":""}}'
            )
        return "我是 Mesh。"

    with mock.patch("app.llm.call", side_effect=fake_llm):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            r1 = colleague_v3.handle(
                con=None,
                user_text="创建个文档详细介绍你自己",
                identity=_ident(),
                permission=_perm_all(),
                context=_ctx(),
                session=st,
                invoke_tool=toolsmod.invoke_tool,
                render_tool_result=lambda r, i, s: (
                    str((r.payload or {}).get("answer") or "ok"),
                    [],
                    list(r.evidence_refs or []),
                ),
            )
            assert r1.intent == "feishu_write"
            assert st.active_goal and st.active_goal.get("family") == "feishu_write"
            assert st.pending_write

            r2 = colleague_v3.handle(
                con=None,
                user_text="确认",
                identity=_ident(),
                permission=_perm_all(),
                context=_ctx(),
                session=st,
                invoke_tool=toolsmod.invoke_tool,
                render_tool_result=lambda r, i, s: (
                    "已创建 https://feishu.cn/docx/mock_created",
                    [],
                    [],
                ),
            )
    assert r2.tools_called == ["feishu.doc.create"]
    assert st.active_goal is None
    assert st.last_block is None
    assert st.pending_write is None


def test_confirm_fail_sets_last_block():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    st = SessionContextState()
    st.pending_write = {
        "tool": "feishu.doc.create",
        "args": {"title": "x", "content": "y", "confirmed": False},
    }
    st.active_goal = {
        "summary": "写文档",
        "family": "feishu_write",
        "tool": "feishu.doc.create",
        "utterance": "创建文档",
    }

    def boom_invoke(tool, *a, **k):
        return ToolResult(ok=False, tool_id=tool, error="feishu_api_99991672: scope denied", payload={})

    with mock.patch("app.llm.call", return_value="ok"):
        r = colleague_v3.handle(
            con=None,
            user_text="确认",
            identity=_ident(),
            permission=_perm_all(),
            context=_ctx(),
            session=st,
            invoke_tool=boom_invoke,
            render_tool_result=lambda r, i, s: ("", [], []),
        )
    assert "没写进去" in r.text or "权限" in r.text
    assert st.last_block and st.last_block.get("code") == "scope_denied"
    assert st.active_goal is not None  # 失败不清除目标


def test_speak_cannot_fake_create():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    st = SessionContextState()

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return '{"situation":"chat","action":"speak"}'
        return "正在创建文档，稍等。"

    with mock.patch("app.llm.call", side_effect=fake_llm):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            r = colleague_v3.handle(
                con=None,
                user_text="哈哈今天怎么样",
                identity=_ident(),
                permission=_perm_all(),
                context=_ctx(),
                session=st,
                invoke_tool=toolsmod.invoke_tool,
                render_tool_result=lambda r, i, s: ("", [], []),
            )
    assert r.action == "speak"
    assert "没有真正写入" in r.text
