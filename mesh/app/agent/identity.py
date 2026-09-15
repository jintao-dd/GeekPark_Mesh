"""② Identity：open_id / mesh_user → IdentityResult（无飞书通讯录副作用）。"""
from __future__ import annotations

from typing import Any

from .. import ingest
from .models import (
    STATUS_AMBIGUOUS,
    STATUS_ANONYMOUS_WEB,
    STATUS_BOUND,
    STATUS_BOUND_TEAM_MISSING,
    STATUS_OPEN_ID_MISMATCH,
    STATUS_UNLINKED,
    AgentEnvelope,
    IdentityResult,
)

# 不可作为 Person.primary_team 的占位 / 外部桶
_NON_BUSINESS = frozenset({"", "其他", "内容中心·数据聚合", "外部媒体"})

# 多队时软选优先级（飞书映射 / 花名册同时命中多队时用；不锁死 conflict）
_TEAM_PICK_ORDER = (
    "CEO / 总裁办",
    "品牌创意团队",
    "编辑部",
    "投资团队",
    "商业化团队",
    "视频号团队",
    "音频播客团队",
    "社群",
    "Global Partnership",
)


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


def _prefer_team(candidates: list[str]) -> str:
    pool = [c for c in candidates if c]
    if not pool:
        return ""
    for pref in _TEAM_PICK_ORDER:
        if pref in pool:
            return pref
    return pool[0]


def reconcile_primary_team(
    mapped_teams: list[str],
    mesh_users_team: str | None,
) -> tuple[str | None, str]:
    """统一主队选择：users / 飞书映射 / 花名册同一套规则。

    不再因多队或 mesh≠mapped 锁死为 conflict（同事轨要能继续查数）。
    飞书/映射与 users.team 不一致时：优先飞书映射队，source=feishu_over_mesh。
    """
    mapped: list[str] = []
    for t in mapped_teams or []:
        n = normalize_team(t)
        if n and n not in mapped:
            mapped.append(n)
    mesh = normalize_team(mesh_users_team)

    if mesh and mapped:
        if mesh in mapped:
            return mesh, "both_agree"
        # 不一致：组织映射优先（飞书树权威），不 lock
        return mapped[0] if len(mapped) == 1 else _prefer_team(mapped), "feishu_over_mesh"
    if len(mapped) == 1:
        return mapped[0], "feishu_map"
    if len(mapped) > 1:
        return _prefer_team(mapped), "multi_pick"
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


def _identity_from_directory(
    *,
    open_id: str,
    mapped_in: list[str],
    contact_sync: str,
    channel: str,
    chat_id: str,
) -> IdentityResult:
    """open_id 已由飞书事件认证；花名册/通讯录能对上则视为已识别同事。

    不要求先在 Mesh users 表 OAuth 绑定。未知 open_id 仍返回 unlinked。
    """
    from . import person_resolve as pr

    hit = pr.lookup_by_open_id(open_id)
    if not hit or not hit.get("name"):
        return IdentityResult(
            status=STATUS_UNLINKED,
            feishu_open_id=open_id,
            contact_sync=contact_sync,
            bind_state="unlinked",
            channel=channel,
            chat_id=chat_id,
        )

    roster_teams: list[str] = []
    for part in str(hit.get("teams") or "").split(","):
        n = normalize_team(part.strip())
        if n and n not in roster_teams:
            roster_teams.append(n)

    mapped = [normalize_team(t) or t for t in (mapped_in or []) if normalize_team(t)]
    if not mapped:
        mapped = list(roster_teams)

    primary, src = reconcile_primary_team(mapped, None)
    if roster_teams and src in ("feishu_map", "multi_pick", "none"):
        src = "roster_multi" if len(mapped) > 1 else ("roster" if primary else "none")
    status = STATUS_BOUND if primary else STATUS_BOUND_TEAM_MISSING

    name = str(hit.get("name") or "").strip()
    person: dict[str, Any] = {
        "display": name,
        "name": name,
        "feishu_open_id": open_id,
        "source": hit.get("source") or "roster",
    }
    if hit.get("job_title"):
        person["job_title"] = hit["job_title"]
    if hit.get("employee_no"):
        person["employee_no"] = hit["employee_no"]

    sync = contact_sync
    if sync in ("", "skipped_no_scope"):
        sync = "roster_known"

    return IdentityResult(
        status=status,
        mesh_user_id=None,
        feishu_open_id=open_id,
        mesh_role="viewer",
        primary_team=primary,
        mapped_teams=mapped,
        mesh_users_team=None,
        team_source=src,
        contact_sync=sync,
        bind_state="roster_known",
        channel=channel,
        chat_id=chat_id,
        display_hint=name,
        person=person,
    )


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
    # 信封未带映射时，按飞书 Department → Mesh 业务队表解析
    if not mapped_in and open_id:
        try:
            from .dept_team_map import mapped_teams_for_open_id

            mapped_in = list(mapped_teams_for_open_id(open_id) or [])
            if mapped_in and contact_sync in ("", "skipped_no_scope"):
                contact_sync = "ok"
        except Exception:
            pass

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
            # users 未绑：飞书 open_id 仍可在花名册/通讯录认出 → 同事身份
            return _identity_from_directory(
                open_id=open_id,
                mapped_in=mapped_in,
                contact_sync=contact_sync,
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
    # 不再锁死 bound_team_conflict：飞书映射 / 多队时软选，继续放行查数
    if not primary:
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
