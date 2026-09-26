"""期号日期规则。"""
from __future__ import annotations

from app.issue_period import (
    format_period_label,
    issue_display_date,
    issue_period_date,
    period_fields_for_stamp,
)


def test_published_uses_publish_day():
    d = issue_period_date({
        "status": "published",
        "published_at": "2026-09-08 17:59",
        "updated_at": "2026-09-08 17:59",
        "date_end": "2026-09-08",
    })
    assert d and d.isoformat() == "2026-09-08"
    assert issue_display_date({
        "status": "published",
        "published_at": "2026-09-08 17:59",
        "updated_at": "2026-09-08 17:59",
    }) == "2026.9.8"


def test_published_update_uses_newer_update_day():
    issue = {
        "status": "published",
        "published_at": "2026-09-08 17:59",
        "updated_at": "2026-09-16 04:46",
        "date_end": "2026-09-08",
        "period_label": "2026.9.8",
    }
    d = issue_period_date(issue)
    assert d and d.isoformat() == "2026-09-16"
    assert issue_display_date(issue) == "2026.9.16"


def test_same_day_update_keeps_publish_day():
    """同一天上线又改：仍是上线日（upd 不晚于 pub 日）。"""
    issue = {
        "status": "published",
        "published_at": "2026-09-08 17:59",
        "updated_at": "2026-09-08 20:10",
    }
    assert issue_display_date(issue) == "2026.9.8"


def test_draft_uses_date_end():
    assert issue_display_date({
        "status": "draft",
        "date_end": "2026-09-15",
        "period_label": "旧标签",
    }) == "2026.9.15"


def test_draft_update_uses_newer_update_day():
    """草稿重新生成预览后：大日期跟最新更新日期。"""
    assert issue_display_date({
        "status": "draft",
        "date_end": "2026-09-15",
        "updated_at": "2026-09-22 16:50",
        "period_label": "2026.9.15",
    }) == "2026.9.22"


def test_period_fields_for_stamp():
    f = period_fields_for_stamp("2026-09-22 16:08")
    assert f["period_label"] == "2026.9.22"
    assert f["date_start"] == "2026-09-22"
    assert f["date_end"] == "2026-09-22"
    assert format_period_label(__import__("datetime").date(2026, 8, 27)) == "2026.8.27"
