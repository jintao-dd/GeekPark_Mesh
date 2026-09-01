"""问答范围：个人 / 群 / 话题线程 的隔离上下文 + 团队过滤。"""
from __future__ import annotations

import dataclasses
from typing import Any

from . import db, ingest


@dataclasses.dataclass
class AskScope:
    """一次问答的检索与权限范围。"""

    channel: str = "web"  # web | feishu_dm | feishu_group
    slug: str = ""
    team: str = ""
    date_from: str | None = None
    date_to: str | None = None
    user_id: int | None = None
    feishu_open_id: str = ""
    chat_id: str = ""
    thread_id: str = ""
    web_session_id: str = ""
    role: str = "viewer"
    user_team: str = ""

    @property
    def scope_key(self) -> str:
        """会话隔离键：每人 / 每群 / 每话题线程 独立。"""
        if self.channel == "feishu_group":
            base = f"feishu:grp:{self.chat_id or 'unknown'}"
        elif self.channel == "feishu_dm":
            base = f"feishu:dm:{self.feishu_open_id or self.user_id or 'unknown'}"
        else:
            base = f"web:u{self.user_id or 0}"
        if self.thread_id:
            base += f":t{self.thread_id}"
        elif self.web_session_id:
            base += f":s{self.web_session_id}"
        if self.slug:
            base += f":issue{self.slug}"
        return base

    @property
    def team_filter(self) -> str:
        """检索默认团队过滤（空=全公司）。"""
        if self.team:
            return ingest.canonical_team(self.team) or self.team
        return ""

    def cache_key(self, q: str, session_id: str = "", history_len: int = 0) -> str:
        base = f"v2|{self.scope_key}|{self.team_filter}|{self.slug}|{self.date_from or ''}|{self.date_to or ''}|{q}"
        if history_len:
            return f"{base}|s{session_id}|h{history_len}"
        return f"{base}|s{session_id or 'new'}"


def resolve(con, user: dict | None, payload: dict | None = None) -> AskScope:
    """从 Web / 飞书请求解析范围。"""
    p = payload or {}
    u = user or {}
    channel = (p.get("channel") or "web").strip()
    role = (u.get("role") or u.get("r") or "viewer").strip()
    user_team = (u.get("team") or u.get("t") or "").strip()
    user_id = u.get("id")
    feishu_open_id = (u.get("feishu_open_id") or p.get("feishu_open_id") or "").strip()
    chat_id = (p.get("chat_id") or "").strip()
    thread_id = (p.get("thread_id") or p.get("root_message_id") or "").strip()
    web_session_id = (p.get("session_id") or p.get("web_session_id") or "").strip()

    team = (p.get("team") or p.get("team_scope") or "").strip()
    if not team and chat_id:
        bound = db.get_feishu_chat_team(con, chat_id)
        if bound:
            team = bound
    if not team and channel == "feishu_dm" and user_team:
        team = user_team
    if not team and role == "dept" and user_team:
        team = user_team

    slug = (p.get("slug") or "").strip()
    date_from = (p.get("date_from") or "").strip() or None
    date_to = (p.get("date_to") or "").strip() or None
    return AskScope(
        channel=channel,
        slug=slug,
        team=team,
        date_from=date_from,
        date_to=date_to,
        user_id=int(user_id) if user_id else None,
        feishu_open_id=feishu_open_id,
        chat_id=chat_id,
        thread_id=thread_id,
        web_session_id=web_session_id,
        role=role,
        user_team=user_team,
    )


def scope_dict(scope: AskScope) -> dict[str, Any]:
    return dataclasses.asdict(scope)
