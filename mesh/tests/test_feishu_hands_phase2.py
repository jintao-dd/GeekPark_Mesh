"""Feishu Hands Phase 2 — search(doc) skeleton + Brain wiring."""
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
from app.agent.feishu_hands import backends, flags, search as searchmod
from app.agent.feishu_hands.normalize import normalize_docs
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
)
from app.agent.session_state import SessionContextState
from app.agent.tool_contract import SourceTier, TruthLevel, speech_hint


def setup_function():
    backends.clear_search_backend()
    os.environ.pop("MESH_FEISHU_HANDS", None)
    os.environ.pop("MESH_FEISHU_HANDS_WRITE", None)
    os.environ.pop("MESH_FEISHU_HANDS_MCP_URL", None)


def test_hands_default_off():
    assert flags.hands_enabled() is False
    assert flags.write_enabled() is False


def test_acl_without_hands():
    ident = IdentityResult(status="bound", primary_team="编辑部")
    p = permmod.decide_permission(ident)
    assert "feishu.search" not in p.tool_acl


def test_acl_with_hands():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    ident = IdentityResult(status="bound", primary_team="编辑部")
    p = permmod.decide_permission(ident)
    assert "feishu.search" in p.tool_acl


def test_search_disabled():
    env = searchmod.search("周报", resource_type="doc")
    assert env.ok is False
    assert env.error == "hands_disabled"
    assert env.source_tier == SourceTier.FEISHU_LIVE


def test_resource_type_message_blocked():
    os.environ["MESH_FEISHU_HANDS"] = "1"

    def fake(query, **kwargs):
        raise AssertionError("should not call backend")

    backends.set_search_backend(fake)
    env = searchmod.search("x", resource_type="message", phase="2")
    assert env.ok is False
    assert "resource_type_not_allowed" in env.error


def test_search_empty_honest():
    os.environ["MESH_FEISHU_HANDS"] = "1"

    def fake(query, **kwargs):
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok([])

    backends.set_search_backend(fake)
    env = searchmod.search("不存在的文档xyz", resource_type="doc")
    assert env.ok is True
    assert env.empty is True
    assert env.items == []


def test_search_normalize_and_tier():
    os.environ["MESH_FEISHU_HANDS"] = "1"

    def fake(query, **kwargs):
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok(
            normalize_docs(
                [{"title": "Q3 规划", "docs_token": "tok1", "docs_type": "docx", "snippet": "AI"}]
            )
        )

    backends.set_search_backend(fake)
    env = searchmod.search("规划", resource_type="doc")
    assert env.ok and not env.empty
    assert env.truth_level == TruthLevel.LIVE_CONTEXT
    assert env.items[0]["title"] == "Q3 规划"
    assert "feishu.cn" in env.items[0]["url"]


def test_tool_feishu_search_payload_not_published():
    os.environ["MESH_FEISHU_HANDS"] = "1"

    def fake(query, **kwargs):
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok(
            normalize_docs([{"title": "产品纪要", "url": "https://feishu.cn/docx/abc"}])
        )

    backends.set_search_backend(fake)
    ident = IdentityResult(status="bound", primary_team="编辑部", feishu_open_id="ou_x")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["feishu.search", "ask.published"],
        data_visibility={"forbid": ["draft", "raw", "unpublished"]},
        query_scope={"mode": "unfocused", "team_focus": None},
        published_only=True,
    )
    ctx = AgentContext(scope_key="t", channel="harness", issue_ref=IssueRef(mode="none"))
    tr = toolsmod.invoke_tool(
        "feishu.search", None, ident, perm, ctx, {"q": "产品", "resource_type": "doc"}
    )
    assert tr.ok
    assert tr.payload["source_tier"] == "feishu_live"
    assert tr.payload["truth_level"] == "live_context"
    assert tr.claim_bindings == []
    assert "飞书" in speech_hint(SourceTier.FEISHU_LIVE)
    assert "产品纪要" in tr.payload["answer"]


def test_colleague_v3_feishu_search_path():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    st = SessionContextState()

    def fake_llm(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return '{"action":"ask","tool":"feishu.search","query":"Q3 规划"}'
        # synthesize
        assert "live_context" in system or "飞书" in system
        return "飞书最近的文档里有一份《Q3 规划》。"

    def fake_backend(query, **kwargs):
        from app.agent.feishu_hands.normalize import envelope_ok

        return envelope_ok(
            normalize_docs([{"title": "Q3 规划", "url": "https://feishu.cn/docx/1"}])
        )

    backends.set_search_backend(fake_backend)
    ident = IdentityResult(status="bound", primary_team="编辑部", display_hint="小王")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "feishu.search", "context.list_issues"],
        data_visibility={"forbid": ["draft", "raw", "unpublished"]},
        query_scope={"mode": "primary", "team_focus": "编辑部"},
        published_only=True,
    )
    ctx = AgentContext(scope_key="t", channel="harness", issue_ref=IssueRef(mode="none"))

    with mock.patch("app.llm.call", side_effect=fake_llm):
        out = colleague_v3.handle(
            con=None,
            user_text="飞书里有没有 Q3 规划文档",
            identity=ident,
            permission=perm,
            context=ctx,
            session=st,
            invoke_tool=toolsmod.invoke_tool,
            render_tool_result=lambda r, intent, st_: (
                str((r.payload or {}).get("answer") or ""),
                [],
                list(r.evidence_refs or []),
            ),
        )
    assert out.action == "ask"
    assert out.tools_called == ["feishu.search"]
    assert out.trace.get("source_tier") == "feishu_live"
    assert "action" not in out.text
    assert "Q3" in out.text or "规划" in out.text


def test_write_flag_independent():
    os.environ["MESH_FEISHU_HANDS"] = "1"
    assert flags.write_enabled() is False
    os.environ["MESH_FEISHU_HANDS_WRITE"] = "1"
    assert flags.write_enabled() is True
