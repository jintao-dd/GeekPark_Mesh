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
    captured = {}

    def fake_prepare(con, q, scope, history=None, *, search_q=None, context_refs=None):
        captured["q"] = q
        captured["search_q"] = search_q
        captured["team"] = scope.team
        return {
            "contexts": [
                {
                    "期号": "2026-09-08",
                    "章节": "条目",
                    "标题": "赵思琪接触",
                    "内容": "Reverie AI",
                    "条目ID": 1,
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
    assert captured["q"] == "最近周报有和我们团队相关的？"
    assert "赵思琪" in (captured["search_q"] or "")
    assert captured["team"] == ""
    assert "赵思琪" in (out.payload.get("answer") or "") or out.payload.get("n_hits", 0) >= 1
