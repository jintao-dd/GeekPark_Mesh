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
    # Planner 改写后的检索词优先；用户原话只作兜底线索
    assert args["query"] == "近期周报"
    assert "关联这些人的周报" in args["q"]
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


def test_mouth_system_keeps_biz_team_people_despite_weekly_bucket():
    from app.agent.supervisor import mouth

    assert "业务队" in mouth._SYNTH_SYSTEM
    assert "硅谷 BD" in mouth._SYNTH_SYSTEM
    assert "赵思琪" in mouth._SYNTH_SYSTEM
    assert "不要注水" in mouth._SYNTH_SYSTEM


def test_mouth_max_tokens_capped():
    from app.agent.supervisor import mouth

    assert mouth._mouth_max_tokens() <= 2000
    assert mouth._mouth_max_tokens() >= 400


def test_enrich_ask_includes_biz_team_teammates(monkeypatch):
    from app.agent.supervisor import workers
    from app.agent.supervisor.types import PlanStep
    from app.agent.models import IdentityResult

    def fake_our_team(team, *, limit=24):
        return (["赵思琪", "Sean Shen", "胡清远", "杜锦涛"][:limit], team)

    monkeypatch.setattr(
        "app.agent.feishu_hands.org_directory.our_team_members",
        fake_our_team,
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


def test_enrich_prefers_planner_query_over_raw_utterance():
    """Planner 按工作记忆改写的检索词优先；原话不得覆盖它。"""
    from app.agent.supervisor import workers
    from app.agent.supervisor.types import PlanStep
    from app.agent.models import IdentityResult

    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "杜锦涛 最近进展"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(status="bound", display_hint="杜锦涛"),
        context=None,
        user_text="他后来呢",
    )
    assert args["query"] == "杜锦涛 最近进展"
    assert args["q"].startswith("杜锦涛 最近进展")
    assert "【检索线索" in args["q"]
    assert "他后来呢" in args["q"]


def test_enrich_falls_back_to_utterance_when_planner_empty():
    from app.agent.supervisor import workers
    from app.agent.supervisor.types import PlanStep
    from app.agent.models import IdentityResult

    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(status="bound", display_hint="杜锦涛"),
        context=None,
        user_text="最近谁在跟进具身智能",
    )
    assert args["query"] == "最近谁在跟进具身智能"


def test_mouth_history_block_renders_recent_turns():
    from app.agent.supervisor import mouth
    from app.agent.session_state import SessionContextState

    st = SessionContextState(session_key="dm:ou_x")
    st.recent_turns = [
        {"role": "user", "text": "杜锦涛最近怎么样"},
        {"role": "assistant", "text": "他在推具身智能这条线。"},
    ]
    block = mouth._history_block(st)
    assert "此前对话" in block
    assert "杜锦涛最近怎么样" in block
    assert "不是事实来源" in block
    assert mouth._history_block(None) == ""


def test_enrich_about_me_skips_teammate_dump(monkeypatch):
    from app.agent.supervisor import workers
    from app.agent.supervisor.types import PlanStep
    from app.agent.models import IdentityResult

    monkeypatch.setattr(
        "app.agent.feishu_hands.org_directory.our_team_members",
        lambda *a, **k: (["赵思琪", "Sean Shen", "胡清远"], "品牌创意团队"),
    )
    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "列出最近周报和我有关的内容"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(
            status="bound",
            primary_team="品牌创意团队",
            display_hint="杜锦涛",
        ),
        context=None,
        user_text="列出最近周报和我有关的内容",
    )
    names = args.get("person_names") or []
    assert names == ["杜锦涛"]
    assert "赵思琪" not in names


def test_enrich_named_colleague_skips_asker():
    from app.agent.supervisor.types import PlanStep

    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "思琪最近在忙什么？"},
    )
    args = workers.enrich_args(
        step,
        identity=IdentityResult(
            status="bound",
            primary_team="品牌创意团队",
            display_hint="杜锦涛",
        ),
        context=None,
        user_text="思琪最近在忙什么？",
        resolved_names=["赵思琪"],
    )
    names = args.get("person_names") or []
    assert "赵思琪" in names
    assert "杜锦涛" not in names


def test_strip_calendar_unless_asked_progress():
    from app.agent.supervisor.types import PlanStep

    steps = [
        PlanStep(id="a", worker="published", tool="ask.published", args={"query": "进展"}),
        PlanStep(id="b", worker="calendar", tool="feishu.calendar.list", args={}),
        PlanStep(
            id="c",
            worker="org",
            tool="feishu.search",
            args={"resource_type": "calendar"},
            depends_on=["b"],
        ),
    ]
    kept = planmod.strip_calendar_unless_asked(steps, "最近有什么进展")
    assert [s.tool for s in kept] == ["ask.published"]
    kept2 = planmod.strip_calendar_unless_asked(steps, "这周我日历上忙不忙")
    assert len(kept2) == 3


def test_mouth_system_calendar_not_primary_for_progress():
    from app.agent.supervisor import mouth

    assert "日历" in mouth._SYNTH_SYSTEM
    assert "补充" in mouth._SYNTH_SYSTEM


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


def test_supervisor_enabled_default_on(monkeypatch):
    monkeypatch.delenv("MESH_SUPERVISOR", raising=False)
    assert supervisor.supervisor_enabled() is True
    monkeypatch.setenv("MESH_SUPERVISOR", "0")
    assert supervisor.supervisor_enabled() is False


def test_progress_callback_emits_during_work(monkeypatch):
    from app.agent.supervisor import progress as progressmod

    seen: list[str] = []
    token = progressmod.set_progress_callback(lambda lab: seen.append(lab))
    try:
        st = sstore.SessionContextState()
        ident = IdentityResult(status="bound", feishu_open_id="ou_x", primary_team="编辑部")
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

        def fake_invoke(tool, con, identity, permission, context, args):
            return ToolResult(
                ok=True,
                tool_id=tool,
                payload={"source_tier": "published", "text": "hit", "items": []},
            )

        def fake_render(result, intent, status):
            return ("- hit", [], [])

        def fake_call(system, user, max_tokens=4000, json_mode=False, task="default"):
            if json_mode:
                return {
                    "mode": "work",
                    "band": "ordinary",
                    "goal": "周报",
                    "steps": [
                        {
                            "id": "s1",
                            "worker": "published",
                            "tool": "ask.published",
                            "args": {"q": "进展"},
                        }
                    ],
                }
            return "整理好了。"

        monkeypatch.setattr("app.llm.call", fake_call)
        monkeypatch.setattr("app.llm.model_for_task", lambda *a, **k: "mock")
        monkeypatch.setattr(
            "app.agent.supervisor.mouth.synthesize_work",
            lambda *a, **k: ("答", {"llm_used": False}, {"FACT": "x"}),
        )
        out = supervisor.handle_turn(
            con=None,
            user_text="最近周报有什么",
            identity=ident,
            permission=perm,
            context=ctx,
            session=st,
            invoke_tool=fake_invoke,
            render_tool_result=fake_render,
        )
        assert out.action == "ask"
        assert any("周报" in p for p in (out.progress or seen))
        assert seen  # callback fired
    finally:
        progressmod.reset_progress_callback(token)

def test_write_gate_prepare_and_confirm_via_writer(monkeypatch):
    from app.agent.supervisor import write_gate
    from app.agent.supervisor.types import TaskGraph

    st = sstore.SessionContextState()
    ident = IdentityResult(status="bound", feishu_open_id="ou_x", primary_team="编辑部")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["feishu.doc.create"],
        data_visibility={"published_only": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        chat_id="oc_chat",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )

    monkeypatch.setattr("app.agent.feishu_hands.hands_enabled", lambda: True)
    monkeypatch.setattr("app.agent.feishu_hands.write_enabled", lambda: True)
    monkeypatch.setattr(
        "app.agent.supervisor.mouth.speak",
        lambda *a, **k: ("文档正文草稿", {"llm_used": False}),
    )

    prep = write_gate.run_write(
        mode="prepare_write",
        graph=TaskGraph(
            goal="写文档",
            mode="prepare_write",
            write_tool="feishu.doc.create",
            write_args={"title": "测试"},
        ),
        con=None,
        user_text="帮我创建飞书文档介绍 Mesh",
        identity=ident,
        permission=perm,
        context=ctx,
        session=st,
        invoke_tool=lambda *a, **k: None,
        render_tool_result=lambda *a, **k: ("", [], []),
    )
    assert prep.trace.get("supervisor_owned_write") is True
    assert st.pending_write and st.pending_write.get("tool") == "feishu.doc.create"
    assert "确认" in (prep.text or "")

    calls: list[str] = []

    def fake_invoke(tool, con, identity, permission, context, args):
        calls.append(tool)
        assert args.get("confirmed") is True
        return ToolResult(
            ok=True,
            tool_id=tool,
            payload={"meta": {"url": "https://example.com/doc"}, "text": "created"},
        )

    def fake_render(result, intent, status):
        return ("文档已创建", [], [])
    
    monkeypatch.setattr(
        "app.agent.supervisor.mouth.speak",
        lambda *a, **k: ("写好了，文档已创建。", {"llm_used": False}),
    )
    conf = write_gate.run_write(
        mode="confirm_write",
        graph=TaskGraph(goal="确认", mode="confirm_write"),
        con=None,
        user_text="确认",
        identity=ident,
        permission=perm,
        context=ctx,
        session=st,
        invoke_tool=fake_invoke,
        render_tool_result=fake_render,
    )
    assert calls == ["feishu.doc.create"]
    assert conf.trace.get("writer_envelope")
    assert st.pending_write is None
    assert "example.com" in (conf.text or "") or "写好了" in (conf.text or "")


def test_thinking_card_shows_progress():
    from app.agent import feishu_cards

    body = feishu_cards.stage_copy(
        query="查一下",
        progress=["正在查组织/群成员", "正在查日历"],
        stage_index=1,
        tick=1,
    )
    assert "组织" in body
    assert "正在：" in body
    assert "\u280b" in body or "\u2819" in body  # Braille spinner frame
    card = feishu_cards.thinking_card(
        query="查一下", stage=1, progress=["正在查已上线周报"]
    )
    assert "周报" in str(card)
    # 无 progress 时按 stage_index 轮播
    body2 = feishu_cards.stage_copy(query="查一下", stage_index=3, tick=0)
    assert "检索" in body2 or "查" in body2

