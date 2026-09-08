# -*- coding: utf-8 -*-
"""Temporal Phase 1 unit tests — no RAG, no LLM."""
from app.agent import temporal as t


def test_intent_recent_and_weeks():
    assert t.resolve_time_intent("锦涛最近做了什么") == "recent"
    assert t.resolve_time_intent("本周编辑部关注了什么") == "this_week"
    assert t.resolve_time_intent("上周商业化团队跟进了哪些客户") == "last_week"
    assert t.resolve_time_intent("只看 2026-8-17 这一期") == "explicit"


def test_basis_unknown_for_recent_without_event_time():
    sem = t.resolve_time_semantics(
        "锦涛最近做了什么",
        issue_mode="latest_published",
        issue_slug="2026-09-08",
        has_event_time=False,
    )
    assert sem.window == "recent"
    assert sem.basis == "unknown"
    # latest_published + recent → TimeWindow 控检索，可跨已发布
    assert sem.filter_mode == "time_window"
    assert t.retrieval_slug_for_scope(sem, "2026-09-08") == ""


def test_explicit_issue_still_anchors():
    sem = t.resolve_time_semantics(
        "锦涛最近做了什么",
        issue_mode="explicit",
        issue_slug="2026-8-17",
        has_event_time=False,
    )
    assert sem.filter_mode == "issue_anchor"
    assert t.retrieval_slug_for_scope(sem, "2026-8-17") == "2026-8-17"


def test_this_week_keeps_issue_anchor_under_latest():
    sem = t.resolve_time_semantics(
        "本周有没有可同步的关系",
        issue_mode="latest_published",
        issue_slug="2026-09-08",
    )
    assert sem.window == "this_week"
    assert sem.basis == "issue_time"
    assert sem.filter_mode == "issue_anchor"


def test_time_window_dates_from_parse_window():
    sem = t.resolve_time_semantics(
        "锦涛最近做了什么",
        issue_mode="latest_published",
        issue_slug="2026-09-08",
    )
    df, dt = t.apply_time_filter_to_dates(
        sem,
        issue_date_from="2026-09-08",
        issue_date_to="2026-09-08",
        query="锦涛最近做了什么",
    )
    assert df and df < "2026-09-08"
    assert dt is None  # recent 上界不限


def test_hard_rule_strips_recent_event_claim():
    sem = t.resolve_time_semantics(
        "锦涛最近做了什么",
        issue_mode="explicit",
        issue_slug="2026-8-17",
    )
    out = t.apply_hard_rules("锦涛最近发生了一次讨论，本周还在推进。", sem)
    assert "最近发生" not in out
    assert "本周还在" not in out
    assert "2026-8-17" in out or "资料记载" in out


def test_reject_latest_equals_recent_direct():
    sem = t.resolve_time_semantics(
        "把最新上线的期次内容都当成这周刚发生的事总结一下",
        issue_mode="latest_published",
        issue_slug="2026-09-08",
    )
    assert sem.reject_latest_equals_recent
    ans = t.maybe_direct_answer(
        "把最新上线的期次内容都当成这周刚发生的事总结一下", sem
    )
    assert ans and "不等于" in ans


def test_this_week_uses_issue_time_basis():
    sem = t.resolve_time_semantics(
        "本周有没有可同步的关系",
        issue_mode="latest_published",
        issue_slug="2026-09-08",
    )
    assert sem.window == "this_week"
    assert sem.basis == "issue_time"
