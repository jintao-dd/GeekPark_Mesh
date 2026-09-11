"""MeshSupervisor — global assign/supervise + workers."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import session_state as sstore
from app.agent import supervisor
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
    ToolResult,
)
from app.agent.supervisor import plan as planmod
from app.agent.supervisor import workers


def setup_function():
    sstore.reset_for_tests()


def test_resolve_worker_org_vs_research():
    assert workers.resolve_worker("feishu.search", {"resource_type": "group"}) == "org"
    assert workers.resolve_worker("feishu.search", {"resource_type": "doc"}) == "research"
    assert workers.resolve_worker("ask.published", {}) == "published"
    assert workers.resolve_worker("feishu.calendar.list", {}) == "calendar"


def test_supervisor_complex_work_path():
    st = sstore.SessionContextState()
    ident = IdentityResult(
        status="bound",
        feishu_open_id="ou_x",
        primary_team="编辑部",
        display_hint="小王",
    )
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "feishu.search", "feishu.calendar.list"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )
    calls: list[str] = []

    def fake_invoke(tool, con, identity, permission, context, args):
        calls.append(tool)
        tier = "published" if tool.startswith("ask.") else "feishu_live"
        return ToolResult(
            ok=True,
            tool_id=tool,
            payload={
                "source_tier": tier,
                "text": f"mock hit for {tool}",
                "items": [{"title": f"mock:{tool}", "snippet": "ok"}],
            },
        )

    def fake_render(result, intent, status):
        return (f"- {getattr(result, 'payload', {}).get('text') or 'hit'}", [], [])

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return {
                "mode": "work",
                "band": "complex",
                "goal": "群成员日历周报",
                "steps": [
                    {
                        "id": "s1",
                        "tool": "feishu.search",
                        "args": {"resource_type": "group"},
                    },
                    {
                        "id": "s2",
                        "tool": "feishu.search",
                        "args": {"resource_type": "member"},
                        "depends_on": ["s1"],
                    },
                    {
                        "id": "s3",
                        "tool": "ask.published",
                        "args": {"query": "近期协作"},
                        "parallel_group": "fanout",
                    },
                    {
                        "id": "s4",
                        "tool": "feishu.calendar.list",
                        "args": {},
                        "parallel_group": "fanout",
                    },
                ],
            }
        return "不应走到 speak"

    q = "帮我看看我能进哪些群、群里有谁、他们日历怎样，再关联近两期周报里相关的事，哪些值得盯"
    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            out = supervisor.handle_turn(
                con=None,
                user_text=q,
                identity=ident,
                permission=perm,
                context=ctx,
                session=st,
                invoke_tool=fake_invoke,
                render_tool_result=fake_render,
            )
    assert out.trace.get("supervisor") is True
    assert out.action == "ask"
    assert calls
    assert "feishu.search" in calls
    assert "ask.published" in calls
    assert "**我查到的**" in (out.text or "")
    assert out.progress
    assert out.payload.get("supervised") is True


def test_supervisor_speak_path():
    st = sstore.SessionContextState()
    ident = IdentityResult(status="bound", primary_team="编辑部", display_hint="小王")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return {"mode": "speak", "band": "simple", "goal": "闲聊", "steps": []}
        return "懂，这种日子是挺磨人。"

    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            out = supervisor.handle_turn(
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
    assert "磨人" in out.text
    assert "action" not in out.text


def test_plan_rejects_write_in_steps():
    steps = planmod._normalize_steps(
        [
            {"id": "s1", "tool": "feishu.doc.create", "args": {"title": "x"}},
            {"id": "s2", "tool": "ask.published", "args": {"query": "x"}},
        ],
        goal="建文档",
    )
    assert len(steps) == 1
    assert steps[0].tool == "ask.published"
