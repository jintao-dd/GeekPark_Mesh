"""Published lane 写入边界：非 Publish 不得 UPDATE published_json。

架构原则
--------
Draft Lane:  素材 → Mining → Preview → Edit  → 只写 draft_json
Publish:     唯一合法入口 → published_json + Ask index
Reader/Ask:  只读 published 投影

运行时：MeshConnection.execute 拦截 UPDATE … published_json（须 allow_published_write）。
静态：tests/test_published_write_boundary.py 扫描 app 源码白名单。
"""
from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from typing import Iterator

_ALLOW = contextvars.ContextVar("mesh_allow_published_json_write", default=False)

# UPDATE issues SET … published_json …
_UPDATE_PUBLISHED = re.compile(
    r"\bUPDATE\s+issues\s+SET\b[\s\S]*?\bpublished_json\b",
    re.IGNORECASE,
)


class PublishedWriteForbidden(RuntimeError):
    """非 Publish 车道试图写入 published_json。"""


def published_write_allowed() -> bool:
    return bool(_ALLOW.get())


@contextmanager
def allow_published_write(reason: str = "publish") -> Iterator[None]:
    """仅 Publish（及显式恢复线上投影的测试）可进入。"""
    token = _ALLOW.set(True)
    try:
        yield
    finally:
        _ALLOW.reset(token)


def guard_sql_against_published_write(sql: str) -> None:
    """在 DB execute 前调用。只拦 UPDATE；INSERT（seed/测试）放行。"""
    if not sql:
        return
    if not _UPDATE_PUBLISHED.search(sql):
        return
    if published_write_allowed():
        return
    raise PublishedWriteForbidden(
        "published_json 写入被拒绝：仅 Publish 车道允许 UPDATE。"
        f" reason_hint=call allow_published_write(); sql={sql[:160]!r}"
    )


def write_draft_json(con, issue_id: int, payload: str, stamp: str) -> None:
    """编辑/预览唯一写草稿入口（不碰 published_json）。

    抬 updated_at 时按期号规则同步 period_label/date_* 为最新更新日期
    （草稿与已上线改稿一致：大日期跟更新日）。
    """
    from .issue_period import period_fields_for_stamp

    period = period_fields_for_stamp(stamp)
    con.execute(
        "UPDATE issues SET draft_json=?, updated_at=?, period_label=?, date_start=?, date_end=? WHERE id=?",
        (
            payload,
            stamp,
            period["period_label"],
            period["date_start"],
            period["date_end"],
            issue_id,
        ),
    )


def write_publish_projection(
    con,
    issue_id: int,
    *,
    pub_payload: str,
    draft_payload: str,
    published_at: str,
    updated_at: str,
    period_label: str,
    date_end: str,
    date_start: str,
) -> None:
    """Publish 唯一写 published_json 入口（须在 allow_published_write 内）。"""
    if not published_write_allowed():
        raise PublishedWriteForbidden("write_publish_projection 须在 allow_published_write 内调用")
    con.execute(
        "UPDATE issues SET published_json=?, draft_json=?, status='published', published_at=?, "
        "updated_at=?, period_label=?, date_end=?, date_start=? WHERE id=?",
        (
            pub_payload,
            draft_payload,
            published_at,
            updated_at,
            period_label,
            date_end,
            date_start,
            issue_id,
        ),
    )
