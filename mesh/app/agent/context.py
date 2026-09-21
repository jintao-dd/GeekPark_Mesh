"""④ Context：Query Scope + IssueRef（不重判权限）。"""
from __future__ import annotations

from typing import Any

from .. import db
from .identity import normalize_team
from .models import AgentContext, AgentEnvelope, IdentityResult, IssueRef, PermissionDecision


def _scope_key(envelope: AgentEnvelope, identity: IdentityResult, issue_slug: str = "") -> str:
    channel = envelope.channel or "web"
    if channel == "feishu_group":
        base = f"feishu:grp:{envelope.chat_id or 'unknown'}"
    elif channel in ("feishu_dm", "harness") and (envelope.feishu_open_id or identity.feishu_open_id):
        oid = envelope.feishu_open_id or identity.feishu_open_id
        base = f"feishu:dm:{oid}"
    else:
        uid = identity.mesh_user_id or envelope.mesh_user_id or 0
        base = f"web:u{uid}"
    if envelope.thread_id:
        base += f":t{envelope.thread_id}"
    elif envelope.session_id:
        base += f":s{envelope.session_id}"
    if issue_slug:
        base += f":issue{issue_slug}"
    return base


def _issue_published(con, slug: str) -> bool:
    row = con.execute(
        "SELECT status, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        return False
    if (row["status"] or "") != "published":
        return False
    return bool((row["published_json"] or "").strip())


def _latest_published_slug(con) -> str:
    row = con.execute(
        "SELECT slug FROM issues WHERE status='published' "
        "AND published_json IS NOT NULL AND TRIM(published_json) != '' "
        "ORDER BY date_end DESC, id DESC LIMIT 1"
    ).fetchone()
    return (row["slug"] if row else "") or ""


def resolve_issue_ref(con, envelope: AgentEnvelope) -> IssueRef:
    """explicit → pinned → latest_published → none。draft/unpublished 不可作 IssueRef。"""
    explicit = (envelope.explicit_issue or "").strip()
    if explicit:
        if _issue_published(con, explicit):
            return IssueRef(
                mode="explicit",
                slug=explicit,
                locked=True,  # explicit 即锁定；latest 不自动 lock
                epoch=1,
                reason="explicit",
            )
        return IssueRef(
            mode="none",
            slug="",
            locked=False,
            epoch=0,
            reason="explicit_not_published",
        )

    pinned = (envelope.pinned_issue or "").strip()
    if pinned:
        if _issue_published(con, pinned):
            return IssueRef(
                mode="pinned",
                slug=pinned,
                locked=False,
                epoch=int(envelope.pinned_issue_epoch or 0),
                reason="pinned",
            )
        return IssueRef(
            mode="none",
            slug="",
            locked=False,
            epoch=0,
            reason="pinned_invalidated",
        )

    latest = _latest_published_slug(con)
    if latest:
        return IssueRef(
            mode="latest_published",
            slug=latest,
            locked=False,
            epoch=0,
            reason="latest_published",
        )
    return IssueRef(mode="none", slug="", locked=False, epoch=0, reason="no_published")


def chat_team_of(con, chat_id: str) -> str:
    if not chat_id:
        return ""
    try:
        bound = db.get_feishu_chat_team(con, chat_id)
        return normalize_team(bound) or ""
    except Exception:
        return ""


def assemble_context(
    con,
    envelope: AgentEnvelope,
    identity: IdentityResult,
    permission: PermissionDecision,
    *,
    chat_team: str = "",
) -> AgentContext:
    """组装 Context；不改写 Identity.primary_team。

    chat_team 可由调用方传入（runtime 已为权限判定查过一次），避免同一条消息
    重复查 feishu_chat_bindings。
    """
    if not chat_team:
        chat_team = chat_team_of(con, envelope.chat_id)
    # Permission 已算 query_scope；若群视角与 primary 不同，以 Permission 为准
    # （decide_permission 已传入 chat_team）
    issue = resolve_issue_ref(con, envelope)
    sk = _scope_key(envelope, identity, issue.slug if issue.mode != "none" else "")
    return AgentContext(
        scope_key=sk,
        channel=envelope.channel or identity.channel or "web",
        chat_id=envelope.chat_id,
        thread_id=envelope.thread_id,
        session_id=envelope.session_id,
        query_scope=dict(permission.query_scope or {}),
        issue_ref=issue,
        chat_team=chat_team,
        text=(envelope.text or "").strip(),
        mentions=list(envelope.mentions or []),
    )
