"""期号日期规则。

规则：
  - 已上线：期号按上线日（published_at）；上线后若更新日更晚，按最新更新日期
  - 草稿：按 date_end（创建默认当天）；若有更新且更新日期更晚，按最新更新日期
    （重新生成预览会抬 updated_at，大日期应跟到当天）

slug 仍是 URL/库内主键，不随期号展示日改写。
"""
from __future__ import annotations

import datetime
from typing import Any


def parse_stamp_date(value: Any) -> datetime.date | None:
    if not value:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.date.fromisoformat(s[:10])
    except ValueError:
        return None


def normalize_issue_slug(value: Any) -> str:
    """把任意日期字符串规范为 YYYY-MM-DD（补零）。空/非法则返回原样。"""
    if not value:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    # 兼容 2026-8-17、2026/8/17、2026.8.17 等历史缺零写法
    import re
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})$", s)
    if m:
        try:
            return datetime.date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    return s


def format_period_label(d: datetime.date) -> str:
    """读者可见期号：2026.8.27（月日不补零）。"""
    return f"{d.year}.{d.month}.{d.day}"


def issue_period_date(issue: dict[str, Any] | None) -> datetime.date | None:
    """计算本期应展示/排序用的期号日期。"""
    if not issue:
        return None
    status = str(issue.get("status") or "").strip()
    published = bool(issue.get("published_at")) or status == "published"
    upd = parse_stamp_date(issue.get("updated_at"))
    if published:
        pub = parse_stamp_date(issue.get("published_at"))
        if pub and upd and upd > pub:
            return upd
        return pub or upd or parse_stamp_date(issue.get("date_end"))
    # 草稿：创建日为 date_end；改稿/重新预览后跟最新更新日期
    end = parse_stamp_date(issue.get("date_end"))
    if end and upd and upd > end:
        return upd
    return end or upd


def issue_display_date(issue: dict[str, Any] | None) -> str:
    d = issue_period_date(issue)
    if d:
        return format_period_label(d)
    if not issue:
        return ""
    return (issue.get("period_label") or issue.get("slug") or "").strip()


def period_fields_for_day(day: datetime.date) -> dict[str, str]:
    iso = day.isoformat()
    return {
        "period_label": format_period_label(day),
        "date_start": iso,
        "date_end": iso,
    }


def period_fields_for_stamp(stamp: str | None) -> dict[str, str]:
    day = parse_stamp_date(stamp) or datetime.date.today()
    return period_fields_for_day(day)


def sql_order_published_desc() -> str:
    """已上线列表排序：更新日期优先，否则上线日，否则 date_end。"""
    return (
        "COALESCE(NULLIF(updated_at, ''), NULLIF(published_at, ''), date_end) DESC"
    )
