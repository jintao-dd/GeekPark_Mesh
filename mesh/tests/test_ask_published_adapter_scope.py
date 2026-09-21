"""ask.published：query/q 统一；team_focus 默认不当硬桶过滤。"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.adapters import ask_published, scope_from_agent
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    PermissionDecision,
    STATUS_BOUND,
)


def _ident(team="品牌创意团队"):
    return IdentityResult(
        status=STATUS_BOUND,
        channel="feishu_dm",
        primary_team=team,
        display_hint="杜锦涛",
        feishu_open_id="ou_x",
        mesh_role="viewer",
    )


def _perm(team="品牌创意团队"):
    return PermissionDecision(
        agent_access=True,
        tool_acl=["ask.published"],
        data_visibility={"published_only": True, "forbid": ["draft", "raw"]},
        query_scope={"mode": "primary", "team_focus": team},
        published_only=True,
    )


def _ctx():
    return AgentContext(
        scope_key="t",
        channel="feishu_dm",
        text="",
        issue_ref=IssueRef(mode="latest_published", slug="2026-09-08"),
    )


class _FakeCon:
    def execute(self, sql, params=()):
        class R:
            def fetchone(self_inner):
                if "published_json" in sql:
                    return {"status": "published", "published_json": "{}"}
                return {"date_start": "2026-09-01", "date_end": "2026-09-08"}

        return R()


def test_scope_default_ignores_team_focus_bucket():
    scope, _ = scope_from_agent(
        _ident(),
        _perm(),
        _ctx(),
        con=_FakeCon(),
        query="最近周报有和我们团队相关的？",
    )
    assert scope.team == ""
    assert scope.team_filter == ""


def test_scope_explicit_team_filter_still_works():
    scope, _ = scope_from_agent(
        _ident(),
        _perm(),
        _ctx(),
        con=_FakeCon(),
        query="视频号团队本周数据",
        team_filter="视频号团队",
    )
    assert scope.team == "视频号团队"


def test_ask_published_reads_query_and_person_names():
    captured = {"calls": []}

    def fake_prepare(con, q, scope, history=None, *, search_q=None, context_refs=None):
        captured["calls"].append({"q": q, "search_q": search_q, "team": scope.team})
        return {
            "contexts": [
                {
                    "期号": "2026-09-08",
                    "章节": "条目",
                    "标题": "赵思琪接触",
                    "内容": "Reverie AI",
                    "条目ID": len(captured["calls"]),
                }
            ],
            "n_hits": 1,
            "n_context": 1,
            "mode": "lexical",
            "search_q": search_q or q,
        }

    with mock.patch("app.agent.adapters.ask_engine.prepare", fake_prepare), mock.patch(
        "app.agent.claim_support.assess_claim_support",
        return_value={"support": "supported", "reason": "ok"},
    ), mock.patch(
        "app.agent.claim_support.abstain_answer_for_unsupported_claim",
        return_value=None,
    ), mock.patch(
        "app.agent.claim_support.enrich_contexts_for_denial_counter_evidence",
        side_effect=lambda *a, **k: a[3] if len(a) > 3 else k.get("contexts") or [],
    ):
        out = ask_published(
            _FakeCon(),
            _ident(),
            _perm(),
            _ctx(),
            {
                "query": "最近周报有和我们团队相关的？",
                "person_names": ["赵思琪", "杜锦涛"],
            },
        )
    assert out.ok
    assert captured["calls"][0]["q"] == "最近周报有和我们团队相关的？"
    assert captured["calls"][0]["search_q"] == "最近周报有和我们团队相关的？"
    assert captured["calls"][0]["team"] == ""
    # expand batches follow
    assert any("赵思琪" in str(c.get("search_q") or "") for c in captured["calls"][1:])
    assert out.payload.get("n_hits", 0) >= 1


def test_expand_person_contexts_batches_names():
    calls = []

    def fake_prepare(con, q, scope, history=None, *, search_q=None, context_refs=None, use_vector=True):
        calls.append({"sq": search_q or q, "use_vector": use_vector})
        return {
            "contexts": [
                {
                    "期号": "2026-09-08",
                    "章节": "条目",
                    "标题": search_q or q,
                    "内容": "x",
                    "条目ID": len(calls),
                }
            ],
            "n_hits": 1,
            "n_context": 1,
            "mode": "lexical",
        }

    from app.ask_scope import AskScope
    from app.agent import adapters as ad

    with mock.patch("app.agent.adapters.ask_engine.prepare", fake_prepare):
        ctxs = ad._expand_person_contexts(
            None,
            AskScope(),
            "最近周报有和我们团队相关的？",
            ["赵思琪", "杜锦涛", "张山山", "Sean Shen", "胡清远", "彭康林"],
        )
    assert len(calls) >= 2
    assert all("最近周报" not in c["sq"] for c in calls)
    # 人名扩召回不得重复走向量（避免每批一次全量 cosine）
    assert all(c["use_vector"] is False for c in calls)
    assert len(ctxs) >= 2
