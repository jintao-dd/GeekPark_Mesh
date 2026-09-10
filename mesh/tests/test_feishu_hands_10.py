"""Feishu Hands 10 能力 — Contract + mock 路径 + 确认写。"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import colleague_v3
from app.agent import permission as permmod
from app.agent import tools as toolsmod
from app.agent.feishu_hands import backends, flags, ops, search as searchmod
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
)
from app.agent.session_state import SessionContextState
from app.agent.tool_contract import (
    FEISHU_ALL_TOOLS,
    FEISHU_WRITE_TOOLS,
    REGISTRY,
    SourceTier,
    feishu_search_type_allowed,
)


def setup_function():
    backends.clear_ops_backend()
    backends.clear_search_backend()
    os.environ.pop("MESH_FEISHU_HANDS", None)
    os.environ.pop("MESH_FEISHU_HANDS_WRITE", None)
    os.environ.pop("MESH_FEISHU_HANDS_BACKEND", None)
    os.environ.pop("MESH_FEISHU_HANDS_MCP_URL", None)


def _ident():
    return IdentityResult(status="bound", primary_team="编辑部", display_hint="小王", feishu_open_id="ou_x")


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
    return AgentContext(scope_key="t", channel="harness", issue_ref=IssueRef(mode="none"))


def test_registry_has_ten_capabilities():
    # 1 search 2 message via search 3 calendar.list 4 discuss 5 doc.get 6 wiki via search
    # 7 speak(no tool) 8 doc.create 9 im.send 10 calendar.create
    for name in (
        "feishu.search",
        "feishu.doc.get",
        "feishu.calendar.list",
        "feishu.discuss.summary",
        "feishu.doc.create",
        "feishu.im.send",
        "feishu.calendar.create",
    ):
        assert name in REGISTRY
    for w in FEISHU_WRITE_TOOLS:
        assert REGISTRY[w].confirmation_required is True
        assert REGISTRY[w].side_effect.value == "write"


def test_search_types_full():
    for rt in ("doc", "message", "group", "wiki", "folder", "calendar"):
        assert feishu_search_type_allowed(rt, phase="full")
    assert not feishu_search_type_allowed("message", phase="2")


def test_mock_all_reads_and_writes():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"

    assert searchmod.search("规划", resource_type="doc").ok
    assert searchmod.search("聊天", resource_type="message").ok
    assert searchmod.search("知识库", resource_type="wiki").ok
    assert searchmod.search("文件夹", resource_type="folder").ok
    assert ops.doc_get(query="纪要").ok
    assert ops.calendar_list(query="周会").ok
    assert ops.discuss_summary(query="张三", person="张三").ok

    # write without confirm blocked
    bad = ops.doc_create(title="t", content="c", confirmed=False)
    assert bad.ok is False
    assert bad.error == "confirmation_required"

    ok = ops.doc_create(title="t", content="c", confirmed=True)
    assert ok.ok and ok.items
    assert ops.im_send(receive_id="oc_x", text="hi", confirmed=True).ok
    assert ops.calendar_create(
        title="同步", start="2026-09-11T10:00:00", end="2026-09-11T11:00:00", confirmed=True
    ).ok


def test_write_flag_blocks_even_with_confirm():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "0"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    env = ops.doc_create(title="t", content="c", confirmed=True)
    assert env.ok is False
    assert env.error == "write_disabled"


def test_acl_includes_write_only_when_write_on():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "0"
    p = permmod.decide_permission(_ident())
    assert "feishu.search" in p.tool_acl
    assert "feishu.doc.create" not in p.tool_acl
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    p2 = permmod.decide_permission(_ident())
    assert "feishu.doc.create" in p2.tool_acl


def test_tools_registry_invoke_search_message():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    tr = toolsmod.invoke_tool(
        "feishu.search",
        None,
        _ident(),
        _perm_all(),
        _ctx(),
        {"q": "张三", "resource_type": "message"},
    )
    assert tr.ok
    assert tr.payload["source_tier"] == "feishu_live"
    assert tr.claim_bindings == []


def test_prepare_and_confirm_write_flow():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    st = SessionContextState()

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            if "确认" in user or user.strip().endswith("确认"):
                return '{"action":"confirm_write"}'
            return (
                '{"action":"prepare_write","tool":"feishu.doc.create",'
                '"args":{"title":"周报整理","content":"正文A"}}'
            )
        return "已创建。"

    with mock.patch("app.llm.call", side_effect=fake_llm):
        r1 = colleague_v3.handle(
            con=None,
            user_text="帮我整理成飞书文档",
            identity=_ident(),
            permission=_perm_all(),
            context=_ctx(),
            session=st,
            invoke_tool=toolsmod.invoke_tool,
            render_tool_result=lambda r, i, s: (
                str((r.payload or {}).get("answer") or ""),
                [],
                list(r.evidence_refs or []),
            ),
        )
        assert r1.intent == "feishu_write"
        assert st.pending_write and st.pending_write["tool"] == "feishu.doc.create"
        assert "确认" in r1.text

        r2 = colleague_v3.handle(
            con=None,
            user_text="确认",
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
        assert r2.tools_called == ["feishu.doc.create"]
        assert st.pending_write is None
        assert "action" not in r2.text


def test_hard_confirm_regex_without_llm_decide():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    st = SessionContextState()
    st.pending_write = {
        "tool": "feishu.calendar.create",
        "args": {
            "title": "同步",
            "start": "2026-09-11T10:00:00",
            "end": "2026-09-11T11:00:00",
        },
    }

    def boom(*a, **k):
        raise AssertionError("llm should not be needed for hard confirm")

    with mock.patch("app.llm.call", side_effect=boom):
        # confirm path still synthesizes with llm — only decide is skipped
        pass

    calls = {"n": 0}

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        calls["n"] += 1
        if json_mode:
            raise AssertionError("decide should short-circuit")
        return "日程已建好。"

    with mock.patch("app.llm.call", side_effect=fake_llm):
        r = colleague_v3.handle(
            con=None,
            user_text="确认",
            identity=_ident(),
            permission=_perm_all(),
            context=_ctx(),
            session=st,
            invoke_tool=toolsmod.invoke_tool,
            render_tool_result=lambda r, i, s: ("日程材料", [], []),
        )
    assert r.tools_called == ["feishu.calendar.create"]
    assert calls["n"] == 1  # synthesize only


def test_hard_confirm_strips_feishu_mention():
    """群聊确认常为 '@_user_1 确认'（11 字），旧正则整句匹配失败会掉成 casual。"""
    os.environ["MESH_FEISHU_HANDS"] = "1"
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    os.environ["MESH_FEISHU_HANDS_BACKEND"] = "mock"
    st = SessionContextState()
    st.pending_write = {
        "tool": "feishu.im.send",
        "args": {"receive_id": "oc_x", "text": "hi", "receive_id_type": "chat_id"},
    }

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            raise AssertionError("decide must hard-confirm after mention strip")
        return "已发送。"

    with mock.patch("app.llm.call", side_effect=fake_llm):
        r = colleague_v3.handle(
            con=None,
            user_text="@_user_1 确认",
            identity=_ident(),
            permission=_perm_all(),
            context=_ctx(),
            session=st,
            invoke_tool=toolsmod.invoke_tool,
            render_tool_result=lambda r, i, s: ("ok", [], []),
        )
    assert r.tools_called == ["feishu.im.send"]
    assert st.pending_write is None
    assert r.trace.get("hard_confirm") is True


def test_parse_im_strips_mention_for_confirm_len():
    from app.agent.feishu_bot import parse_im_message

    p = parse_im_message(
        {
            "sender": {"sender_id": {"open_id": "ou_x"}},
            "message": {
                "chat_id": "oc_g",
                "chat_type": "group",
                "message_type": "text",
                "content": '{"text":"@_user_1 确认"}',
            },
        }
    )
    assert p is not None
    assert p["text"] == "确认"
    assert len("@_user_1 确认") == 11


def test_cancel_write():
    st = SessionContextState()
    st.pending_write = {"tool": "feishu.im.send", "args": {"receive_id": "x", "text": "hi"}}
    with mock.patch("app.llm.call", return_value='{"action":"cancel_write"}'):
        r = colleague_v3.handle(
            con=None,
            user_text="取消",
            identity=_ident(),
            permission=_perm_all(),
            context=_ctx(),
            session=st,
            invoke_tool=toolsmod.invoke_tool,
            render_tool_result=lambda *a: ("", [], []),
        )
    # hard cancel regex also works; either way
    assert st.pending_write is None
    assert "取消" in r.text
