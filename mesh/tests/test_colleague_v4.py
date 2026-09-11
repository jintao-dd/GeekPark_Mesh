"""Colleague v4 — Company Understanding + Orchestrator 单元验收。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import company_context as cctx
from app.agent import company_ontology as ont
from app.agent import company_wiki as wiki
from app.agent import colleague_v3
from app.agent import orchestrator as orch
from app.agent import session_state as sstore
from app.agent import tool_contract as tc
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
    ToolResult,
)


def setup_function():
    sstore.reset_for_tests()


def test_source_tier_wiki_ontology():
    assert tc.truth_for_tier(tc.SourceTier.WIKI_CONTEXT) == tc.TruthLevel.COMPANY_CONTEXT
    assert tc.truth_for_tier(tc.SourceTier.ONTOLOGY) == tc.TruthLevel.COMPANY_STRUCTURE
    assert "语境" in tc.speech_hint(tc.SourceTier.WIKI_CONTEXT)


def test_ontology_from_identity():
    ident = IdentityResult(
        status="bound",
        feishu_open_id="ou_x",
        primary_team="编辑部",
        mapped_teams=["编辑部", "产品"],
        display_hint="小王",
    )
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published"],
        data_visibility={"published_only": True},
        query_scope={"mode": "team_focus", "team_focus": "编辑部"},
    )
    o = ont.build_ontology(identity=ident, permission=perm)
    assert o.primary_team == "编辑部"
    assert o.open_id == "ou_x"
    assert "Ontology" in o.prompt_block()
    assert "非企业事实" in o.prompt_block()


def test_wiki_match_mesh():
    w = wiki.load_wiki(query="Mesh Hands 怎么写入飞书")
    slugs = {p.slug for p in w.matched}
    assert "mesh" in slugs or "hands" in slugs
    assert "wiki_context" in w.prompt_block()
    assert "非企业事实" in w.prompt_block()


def test_judge_complex_canary():
    j = orch.judge_complexity(
        "列出我能访问的群聊和群聊人员详情还有人员日历详情，并梳理一下近期周报和这些人有关联的事情。"
    )
    assert j.band == "complex"
    plan = orch.build_plan(
        "列出我能访问的群聊和群聊人员详情还有人员日历详情，并梳理近期周报",
        j,
    )
    tools = [s.tool for s in plan.steps]
    assert "feishu.search" in tools
    assert "ask.published" in tools
    assert "feishu.calendar.list" in tools


def test_judge_simple_no_planner_noise():
    j = orch.judge_complexity("哈哈今天忙死了")
    assert j.band == "simple"
    assert not orch.should_orchestrate(j, "speak")


def test_columns_keep_tiers_separate():
    results = [
        orch.StepResult(
            step_id="a",
            specialist="published",
            tool="ask.published",
            ok=True,
            source_tier="published",
            text="- 周报：项目 X 推进",
        ),
        orch.StepResult(
            step_id="b",
            specialist="research",
            tool="feishu.search",
            ok=True,
            source_tier="feishu_live",
            text="- 群里在聊 Y",
        ),
    ]
    cols = orch.synthesize_columns(
        results,
        plan=orch.TaskPlan(goal="t", band="complex", steps=[]),
    )
    assert "已上线周报" in cols["FACT"]
    assert "飞书 live" in cols["FACT"]
    text = orch.format_columns(cols)
    assert "**我查到的**" in text
    assert "**我的判断**" in text
    assert "已上线周报" in cols["FACT"]
    assert "飞书 live" in cols["FACT"]


def test_handle_orchestrates_complex_with_mock_tools():
    st = sstore.SessionContextState()
    ident = IdentityResult(
        status="bound",
        feishu_open_id="ou_x",
        primary_team="编辑部",
        display_hint="小王",
    )
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=[
            "ask.published",
            "feishu.search",
            "feishu.calendar.list",
        ],
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
        return (f"- {getattr(result, 'text', '')}", [], [])

    def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
        if json_mode:
            return '{"action":"ask","tool":"ask.published","query":"x"}'
        return "不应走到 speak"

    q = "列出我能访问的群聊和群成员，再看看日历，并关联近期周报里相关的事"
    with mock.patch("app.llm.call", side_effect=fake_call):
        with mock.patch("app.llm.model_for_task", return_value="mock"):
            out = colleague_v3.handle(
                con=None,
                user_text=q,
                identity=ident,
                permission=perm,
                context=ctx,
                session=st,
                invoke_tool=fake_invoke,
                render_tool_result=fake_render,
            )
    assert out.trace.get("colleague_v4") is True
    assert out.trace.get("complexity", {}).get("band") == "complex"
    assert "orchestrator" in out.trace
    assert calls  # tools ran
    assert "**我查到的**" in (out.text or "") or "我查到的" in (out.text or "")


def test_sanitize_keeps_colleague_text_with_incidental_action():
    # 正文里偶然出现 "action" 不得整段毁掉
    raw = (
        "**事实**\n飞书侧查到 3 个群。\n\n"
        '**分析**\n其中有个 payload 提到 "action" 字段但那是材料。\n\n'
        "**我的判断**\n先看第一个群。"
    )
    out = colleague_v3._sanitize_user_visible(raw)
    assert "3 个群" in out
    assert "我的判断" in out


def test_sanitize_still_strips_pure_protocol():
    out = colleague_v3._sanitize_user_visible('{"action":"speak","text":"你好啊"}')
    assert out == "你好啊" or "action" not in out


def test_orchestrated_answer_not_rewritten_to_weekly_no_hit():
    from app.agent.feishu_reply import format_display_text
    from app.agent.models import AgentAnswer

    body = (
        "按你的目标，我分几块说：\n\n"
        "**我查到的**\n【飞书 live】\n- 群 A\n\n"
        "【已上线周报】\n我目前没查到已发布的内容能确认这件事。\n\n"
        "**我的判断**\n先看群侧。"
    )
    ans = AgentAnswer(
        text=body,
        intent="feishu_search",
        context={},
        trace={
            "orchestrator": {"band": "complex"},
            "source_tier": "feishu_live",
        },
    )
    text = format_display_text(
        ans,
        payload={
            "columns": {"FACT": "x"},
            "complexity": "complex",
            "source_tier": "feishu_live",
            "orchestrated": True,
        },
    )
    assert "群 A" in text
    assert "我的判断" in text
    assert "这期周报里我没找到能直接回答的内容" not in text
