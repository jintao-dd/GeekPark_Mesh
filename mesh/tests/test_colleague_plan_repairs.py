"""Colleague plan repairs for boss/CRM/org follow-ups."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.supervisor.plan import apply_colleague_repairs
from app.agent.supervisor.types import PlanStep, TaskGraph


def _graph(steps=None, mode="speak"):
    return TaskGraph(goal="g", mode=mode, band="simple", steps=list(steps or []))


def test_boss_related_forces_crm_and_ask():
    g = _graph(
        [
            PlanStep(
                id="s1",
                worker="research",
                tool="feishu.search",
                args={"resource_type": "directory", "keyword": "老板"},
            )
        ],
        mode="work",
    )
    q = "最近和张鹏（老板）相关的事情有哪些？\n\n【人名解析 · 检索用全名】\n- 老板→张鹏（工号G-001）"
    out, notes = apply_colleague_repairs(g, user_text=q)
    tools = [s.tool for s in out.steps]
    assert "crm.search" in tools
    assert "ask.published" in tools
    assert any("person_related_crm" in n for n in notes)
    crm = next(s for s in out.steps if s.tool == "crm.search")
    assert "张鹏" in str(crm.args.get("query") or "")


def test_challenge_reopens_crm():
    session = SimpleNamespace(
        active_entities=["张鹏"],
        recent_turns=[{"role": "assistant", "text": "没有和老板相关的事情"}],
        last_query="最近和老板相关的事情有哪些？",
        active_team="",
    )
    out, notes = apply_colleague_repairs(
        _graph(mode="speak"),
        user_text="那你刚刚为什么说没有和老板相关的事情？",
        session=session,
    )
    assert out.mode == "work"
    assert any(s.tool == "crm.search" for s in out.steps)
    assert "challenge_reopen_crm" in notes


def test_org_roster_strips_ask_and_uses_directory():
    g = _graph(
        [
            PlanStep(
                id="s1",
                worker="research",
                tool="feishu.search",
                args={"resource_type": "directory", "keyword": "硅谷"},
            ),
            PlanStep(
                id="s2",
                worker="published",
                tool="ask.published",
                args={"query": "我所在的部门都有谁"},
            ),
        ],
        mode="work",
    )
    identity = SimpleNamespace(primary_team="品牌创意团队", display_hint="测试", person={})
    out, notes = apply_colleague_repairs(
        g, user_text="我所在的部门都有谁？", identity=identity
    )
    tools = [s.tool for s in out.steps]
    assert "ask.published" not in tools
    assert "feishu.search" in tools
    assert any("org_directory" in n for n in notes)
    feishu = next(s for s in out.steps if s.tool == "feishu.search")
    assert feishu.args.get("keyword") == "品牌创意团队"


def test_subdept_followup_uses_active_team():
    session = SimpleNamespace(
        active_entities=[],
        recent_turns=[],
        last_query="硅谷团队主要都有谁？",
        active_team="硅谷",
    )
    out, notes = apply_colleague_repairs(
        _graph(mode="speak"),
        user_text="我需要详细到子部门",
        session=session,
    )
    assert out.mode == "work"
    feishu = next(s for s in out.steps if s.tool == "feishu.search")
    assert feishu.args.get("include_subdepartments") is True
    assert feishu.args.get("keyword") == "硅谷"
