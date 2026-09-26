"""诊断：追问时检索问句是否真的带上会话上下文。

用户反馈「回答没有根据上下文，更多是按本句话答」。
这里把 supervisor 检索链路里三个可能丢上下文的地方钉住：
  1) loop 拼的 search_user_text（active_team / active_entities / last_query）
  2) workers.enrich_args 的 q / query 契约（Planner 改写 vs 原话线索）
  3) ask_scope_from_agent 是否把会话团队/时间带进 AskScope
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent import session_state as sstore
from app.agent.supervisor import workers
from app.agent.supervisor.types import PlanStep


def setup_function():
    sstore.reset_for_tests()


def test_search_user_text_carries_session_context():
    """loop.handle_turn 给检索喂的问句必须带上一轮团队/人名，否则「他后来呢」必然空转。"""
    import inspect

    from app.agent.supervisor import loop

    src = inspect.getsource(loop.handle_turn)
    assert "search_user_text" in src
    # 会话线索三件套都要参与拼接
    for key in ("active_team", "active_entities", "last_query"):
        assert key in src, f"检索问句丢了会话线索 {key}"


def test_enrich_ask_clue_does_not_pollute_query():
    """Planner 改写词进 query；原话只能进 q 的【检索线索】段，不得覆盖 query。"""
    step = PlanStep(
        id="s1",
        worker="published",
        tool="ask.published",
        args={"query": "具身智能 进展"},
    )
    args = workers.enrich_args(
        step,
        identity=None,
        context=None,
        user_text="他后来呢",
    )
    assert args["query"] == "具身智能 进展"
    assert "【检索线索" in args["q"]
    assert args["q"].split("【检索线索", 1)[0].strip() == "具身智能 进展"


def test_adapter_strips_clue_before_retrieval():
    """adapters 必须把线索段切掉，只留 Planner 改写词做检索。"""
    from app.agent import adapters

    raw = "具身智能 进展\n\n【检索线索】他后来呢"
    user_q = raw.split(adapters._CLUE_MARK, 1)[0].strip()
    assert user_q == "具身智能 进展"


def test_scope_from_agent_keeps_issue_slug_for_followup():
    """追问沿用上一轮期次时，AskScope 不应把 slug 清成空。"""
    from app.agent.adapters import scope_from_agent
    from app.agent.models import AgentContext, IdentityResult, IssueRef, PermissionDecision

    ident = IdentityResult(status="bound", mesh_user_id=1, feishu_open_id="ou_x")
    perm = PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published", "feishu.search"],
        data_visibility={"published": True},
        query_scope={"mode": "all_published"},
    )
    ctx = AgentContext(
        scope_key="t",
        channel="feishu_dm",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
        text="",
    )
    scope, sem = scope_from_agent(ident, perm, ctx, con=None, query="他后来呢")
    assert scope.slug or sem.get("issue_slug") == "2026-09-08"
