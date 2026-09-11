"""② Identity：open_id / mesh_user → IdentityResult（无飞书通讯录副作用）。"""
from __future__ import annotations

from typing import Any

from .. import ingest
from .models import (
    STATUS_AMBIGUOUS,
    STATUS_ANONYMOUS_WEB,
    STATUS_BOUND,
    STATUS_BOUND_TEAM_CONFLICT,
    STATUS_BOUND_TEAM_MISSING,
    STATUS_OPEN_ID_MISMATCH,
    STATUS_UNLINKED,
    AgentEnvelope,
    IdentityResult,
)

# 不可作为 Person.primary_team 的占位 / 外部桶
_NON_BUSINESS = frozenset({"", "其他", "内容中心·数据聚合", "外部媒体"})


def is_business_team(team: str | None) -> bool:
    t = (team or "").strip()
    if not t or t in _NON_BUSINESS:
        return False
    canon = ingest.canonical_team(t) or t
    return canon in ingest.TEAMS and canon not in _NON_BUSINESS


def normalize_team(team: str | None) -> str | None:
    if not is_business_team(team):
        return None
    t = (team or "").strip()
    return ingest.canonical_team(t) or t


def reconcile_primary_team(
    mapped_teams: list[str],
    mesh_users_team: str | None,
) -> tuple[str | None, str]:
    """返回 (primary_team, team_source)。冲突时 primary=None, source=conflict。"""
    mapped = []
    for t in mapped_teams or []:
        n = normalize_team(t)
        if n and n not in mapped:
            mapped.append(n)
    mesh = normalize_team(mesh_users_team)

    if len(mapped) > 1:
        # 多映射互斥且无法独选
        if mesh and mesh in mapped and len(set(mapped)) == 1:
            return mesh, "both_agree"
        if mesh and all(m == mesh for m in mapped):
            return mesh, "both_agree"
        return None, "conflict"

    if len(mapped) == 1 and mesh:
        if mapped[0] == mesh:
            return mapped[0], "both_agree"
        return None, "conflict"
    if len(mapped) == 1:
        return mapped[0], "feishu_map"
    if mesh:
        return mesh, "mesh_users"
    return None, "none"


def _row_to_user(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "username": row["username"],
        "display": row["display"] or row["username"],
        "role": (row["role"] or "viewer").strip(),
        "team": (row["team"] or "").strip(),
        "feishu_open_id": (row["feishu_open_id"] or "").strip(),
    }


def resolve_identity(con, envelope: AgentEnvelope) -> IdentityResult:
    """Harness / 运行时解析。不 upsert 用户；ChatBinding 不进 primary_team。"""
    if envelope.identity_override:
        ov = envelope.identity_override
        return IdentityResult(
            status=str(ov.get("status") or STATUS_UNLINKED),
            mesh_user_id=ov.get("mesh_user_id"),
            feishu_open_id=str(ov.get("feishu_open_id") or envelope.feishu_open_id or ""),
            mesh_role=str(ov.get("mesh_role") or "viewer"),
            primary_team=ov.get("primary_team"),
            mapped_teams=list(ov.get("mapped_teams") or []),
            mesh_users_team=ov.get("mesh_users_team"),
            team_source=str(ov.get("team_source") or "none"),
            contact_sync=str(ov.get("contact_sync") or envelope.contact_sync),
            bind_state=str(ov.get("bind_state") or "linked"),
            channel=envelope.channel,
            chat_id=envelope.chat_id,
            display_hint=str(ov.get("display_hint") or ""),
            person=dict(ov.get("person") or {}),
        )

    open_id = (envelope.feishu_open_id or "").strip()
    channel = (envelope.channel or "web").strip()
    mapped_in = [t for t in (envelope.mapped_teams or []) if t]
    contact_sync = envelope.contact_sync or "skipped_no_scope"

    user = None
    if envelope.mesh_user_id:
        row = con.execute(
            "SELECT id, username, display, role, team, feishu_open_id FROM users WHERE id=?",
            (int(envelope.mesh_user_id),),
        ).fetchone()
        if row:
            user = _row_to_user(row)
            if open_id and user["feishu_open_id"] and user["feishu_open_id"] != open_id:
                return IdentityResult(
                    status=STATUS_OPEN_ID_MISMATCH,
                    mesh_user_id=user["id"],
                    feishu_open_id=open_id,
                    mesh_role=user["role"],
                    contact_sync=contact_sync,
                    bind_state="open_id_mismatch",
                    channel=channel,
                    chat_id=envelope.chat_id,
                    display_hint=user["display"],
                    person={"display": user["display"]},
                )

    if user is None and open_id:
        rows = con.execute(
            "SELECT id, username, display, role, team, feishu_open_id FROM users WHERE feishu_open_id=?",
            (open_id,),
        ).fetchall()
        if len(rows) > 1:
            return IdentityResult(
                status=STATUS_AMBIGUOUS,
                feishu_open_id=open_id,
                contact_sync=contact_sync,
                bind_state="ambiguous",
                channel=channel,
                chat_id=envelope.chat_id,
            )
        if len(rows) == 1:
            user = _row_to_user(rows[0])
        else:
            return IdentityResult(
                status=STATUS_UNLINKED,
                feishu_open_id=open_id,
                contact_sync=contact_sync,
                bind_state="unlinked",
                channel=channel,
                chat_id=envelope.chat_id,
            )

    if user is None:
        return IdentityResult(
            status=STATUS_ANONYMOUS_WEB,
            contact_sync="web_no_feishu" if channel == "web" else contact_sync,
            bind_state="unlinked",
            channel=channel,
            chat_id=envelope.chat_id,
        )

    mesh_team = normalize_team(user["team"])
    primary, src = reconcile_primary_team(mapped_in, mesh_team)
    if src == "conflict":
        status = STATUS_BOUND_TEAM_CONFLICT
    elif not primary:
        status = STATUS_BOUND_TEAM_MISSING
    else:
        status = STATUS_BOUND

    person: dict[str, Any] = {"display": user["display"], "username": user["username"]}
    sync_status = contact_sync
    oid = open_id or user["feishu_open_id"]
    # 按需用应用身份补全通讯录人像（非长期 Sync 表；短时缓存见 org_directory）
    if oid:
        try:
            from .feishu_hands import backends

            env = backends.call_tool(
                "feishu.search",
                {"query": "", "resource_type": "user", "open_ids": [oid], "max_results": 1},
                timeout_sec=8,
                open_id=oid,
            )
            if env.ok and env.items:
                it = env.items[0]
                person = {
                    **person,
                    "display": str(it.get("title") or person.get("display") or ""),
                    "feishu_open_id": oid,
                    "snippet": str(it.get("snippet") or ""),
                }
                sync_status = "ok"
            elif sync_status in ("", "skipped_no_scope"):
                sync_status = "lookup_empty"
        except Exception:
            if sync_status in ("", "skipped_no_scope"):
                sync_status = "lookup_error"

    return IdentityResult(
        status=status,
        mesh_user_id=user["id"],
        feishu_open_id=oid,
        mesh_role=user["role"],
        primary_team=primary,
        mapped_teams=[normalize_team(t) or t for t in mapped_in if normalize_team(t)],
        mesh_users_team=mesh_team,
        team_source=src,
        contact_sync=sync_status,
        bind_state="linked",
        channel=channel,
        chat_id=envelope.chat_id,
        display_hint=str(person.get("display") or user["display"] or ""),
        person=person,
    )
