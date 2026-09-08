# -*- coding: utf-8 -*-
"""Scope Phase 1：IssueRef ⊥ TimeWindow（不改 Tool Contract）。"""
from app.agent.adapters import scope_from_agent
from app.agent.models import (
    AgentContext,
    IdentityResult,
    IssueRef,
    STATUS_ANONYMOUS_WEB,
)
from app.agent.permission import decide_permission


def _identity():
    return IdentityResult(status=STATUS_ANONYMOUS_WEB, channel="harness", mesh_role="viewer")


def _ctx(text: str, *, mode: str, slug: str) -> AgentContext:
    return AgentContext(
        scope_key="test",
        text=text,
        channel="harness",
        issue_ref=IssueRef(mode=mode, slug=slug, locked=False, epoch=0, reason="test"),
    )


class _FakeCon:
    def execute(self, sql, params=()):
        class R:
            def fetchone(self_inner):
                return {"date_start": "2026-09-08", "date_end": "2026-09-08"}

        return R()


def test_latest_recent_clears_retrieval_slug():
    perm = decide_permission(_identity())
    scope, sem = scope_from_agent(
        _identity(),
        perm,
        _ctx("锦涛最近做了什么", mode="latest_published", slug="2026-09-08"),
        con=_FakeCon(),
        query="锦涛最近做了什么",
    )
    assert sem["filter_mode"] == "time_window"
    assert scope.slug == ""
    assert scope.date_from and scope.date_from < "2026-09-08"
    assert sem["issue_slug"] == "2026-09-08"  # Context 锚点仍在


def test_explicit_keeps_slug_lock():
    perm = decide_permission(_identity())
    scope, sem = scope_from_agent(
        _identity(),
        perm,
        _ctx("锦涛最近做了什么", mode="explicit", slug="2026-8-17"),
        con=_FakeCon(),
        query="锦涛最近做了什么",
    )
    assert sem["filter_mode"] == "issue_anchor"
    assert scope.slug == "2026-8-17"


def test_latest_this_week_keeps_anchor():
    perm = decide_permission(_identity())
    scope, sem = scope_from_agent(
        _identity(),
        perm,
        _ctx("本周编辑部关注了什么", mode="latest_published", slug="2026-09-08"),
        con=_FakeCon(),
        query="本周编辑部关注了什么",
    )
    assert sem["filter_mode"] == "issue_anchor"
    assert scope.slug == "2026-09-08"
