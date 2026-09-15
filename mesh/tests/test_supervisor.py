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
        if "分桶材料" in (user or "") or "完整原话" in (user or ""):
            return (
                "**我查到的**\n"
                "飞书侧有群与成员；周报侧见材料。\n\n"
                "**我的判断**\n"
                "先盯交叉项。"
            )
        return "闲聊兜底"

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


def test_enrich_about_me_query_uses_identity():
    from app.agent.supervisor.types import PlanStep

    ident = IdentityResult(
        status="bound",
        feishu_open_id="ou_x",
        primary_team="编辑部",
        display_hint="杜锦涛",
    )
    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "列出最近周报和我有关的内容"},
    )
    args = workers.enrich_args(
        step,
        identity=ident,
        context=None,
        user_text="列出最近周报和我有关的内容",
    )
    assert args["query"] == "列出最近周报和我有关的内容"
    assert "杜锦涛" in (args.get("person_names") or [])


def test_enrich_user_without_open_id_rewrites_directory():
    from app.agent.supervisor.types import PlanStep

    step = PlanStep(
        id="s1",
        worker="org",
        tool="feishu.search",
        args={"resource_type": "user", "query": "张三"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(status="bound", display_hint="小王"),
        context=None,
        user_text="查张三",
    )
    assert args["resource_type"] == "directory"
    assert "张三" in (args.get("keyword") or args.get("query") or "")


def test_enrich_ask_with_prior_member_names():
    from app.agent.supervisor.types import PlanStep, TieredEnvelope

    prior = [
        TieredEnvelope(
            step_id="m1",
            worker="org",
            tool="feishu.search",
            ok=True,
            tier="feishu_live",
            text="- 杜锦涛\n- 张山山",
            payload={"person_names": ["杜锦涛", "张山山", "彭康林"]},
        )
    ]
    step = PlanStep(
        id="s_ask",
        worker="published",
        tool="ask.published",
        args={"query": "近期周报"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(status="bound", display_hint="小王"),
        context=None,
        prior=prior,
        user_text="关联这些人的周报",
    )
    assert "关联这些人的周报" in args["query"]
    assert "杜锦涛" in (args.get("person_names") or [])
    assert "张山山" in (args.get("person_names") or [])


def test_mouth_strips_open_ids():
    from app.agent.supervisor import mouth

    cleaned = mouth._clean_snippet(
        "（组织/群）- 杜锦涛 — ou_fd65363b8ed1e5ddb93dd56e86a35b9b\n"
        "- CCC技术组 oc_788f30e4deab02247b1a524629c5b387"
    )
    assert "ou_" not in cleaned
    assert "oc_" not in cleaned
    assert "杜锦涛" in cleaned


def test_mouth_system_keeps_subtree_people_despite_weekly_bucket():
    from app.agent.supervisor import mouth

    assert "飞书子树" in mouth._SYNTH_SYSTEM or "飞书组织子树" in mouth._SYNTH_SYSTEM
    assert "硅谷 BD" in mouth._SYNTH_SYSTEM
    assert "禁止说成「不属于品牌创意」" in mouth._SYNTH_SYSTEM


def test_enrich_ask_includes_subtree_teammates(monkeypatch):
    from app.agent.supervisor import workers
    from app.agent.supervisor.types import PlanStep
    from app.agent.models import IdentityResult

    def fake_names(query, *, limit=24):
        return ["赵思琪", "Sean Shen", "胡清远", "杜锦涛"]

    monkeypatch.setattr(
        "app.agent.feishu_hands.org_directory.member_names_for_scope",
        fake_names,
    )
    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "最近周报有和我们团队相关的？"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(
            status="bound",
            feishu_open_id="ou_x",
            primary_team="品牌创意团队",
            display_hint="杜锦涛",
        ),
        context=None,
        user_text="最近周报有和我们团队相关的？",
    )
    q = args["query"]
    names = args.get("person_names") or []
    assert q == "最近周报有和我们团队相关的？"
    assert "赵思琪" in names
    assert "Sean Shen" in names
    assert "【检索线索" not in q
    assert "一律算「我们团队相关」" not in q


def test_mouth_hides_protocol_noise():
    from app.agent.supervisor import mouth
    from app.agent.supervisor.types import TaskGraph, TieredEnvelope

    cols = mouth.format_columns(
        [
            TieredEnvelope(
                step_id="a",
                worker="published",
                tool="ask.published",
                ok=True,
                tier="published",
                text="[published/published] 周报有一条推进",
            ),
            TieredEnvelope(
                step_id="b",
                worker="org",
                tool="feishu.search",
                ok=False,
                tier="feishu_live",
                error="open_id_required_for_user",
            ),
        ],
        graph=TaskGraph(goal="t", band="complex", mode="work"),
        partial=True,
        budget_hit="",
    )
    assert "published/published" not in cols["FACT"]
    assert "open_id_required" not in cols["FACT"]
    assert "budget=" not in cols["ANALYSIS"]
    assert "通讯录" in cols["FACT"] or "没查全" in cols["FACT"]
