"""公司通讯录（应用身份 / tenant_access_token）。

飞书 bot 可以用通讯录 OpenAPI 按部门树拉人；不是「做不到」，
此前误把 CLI `contact +search-user`（仅 user）当成唯一入口。

可见范围 = 应用在飞书后台的「通讯录权限范围」；本租户实测可覆盖部门树下全员。
短时进程内缓存，避免每次聊天都全量 walk。

查询支持：
- 人名 / 工号 / 邮箱
- 飞书部门名（含父部门 → 子组成员 rollup）
- Mesh 业务队名（如「品牌创意」→ 品牌创意团队下全部飞书子部门）
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any

from .normalize import envelope_fail, envelope_ok, normalize_docs
from ..tool_contract import ToolResultEnvelope

log = logging.getLogger("mesh.feishu_hands.org")

_LOCK = threading.Lock()
_CACHE: dict[str, Any] = {"at": 0.0, "people": [], "departments": []}
_TTL_SEC = 15 * 60


def _children(native_mod: Any, dept_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {
            "department_id_type": "open_department_id" if dept_id != "0" else "department_id",
            "page_size": 50,
            "fetch_child": "false",
        }
        if page_token:
            params["page_token"] = page_token
        data = native_mod._api(
            "GET",
            f"/open-apis/contact/v3/departments/{dept_id}/children",
            params=params,
        )
        body = data.get("data") or {}
        items = body.get("items") or []
        out.extend([x for x in items if isinstance(x, dict)])
        if not body.get("has_more"):
            break
        page_token = str(body.get("page_token") or "")
        if not page_token:
            break
    return out


def _users_of(native_mod: Any, dept_open_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    page_token = ""
    while True:
        params: dict[str, Any] = {
            "department_id": dept_open_id,
            "department_id_type": "open_department_id",
            "page_size": 50,
            "user_id_type": "open_id",
        }
        if page_token:
            params["page_token"] = page_token
        data = native_mod._api(
            "GET",
            "/open-apis/contact/v3/users/find_by_department",
            params=params,
        )
        body = data.get("data") or {}
        items = body.get("items") or []
        out.extend([x for x in items if isinstance(x, dict)])
        if not body.get("has_more"):
            break
        page_token = str(body.get("page_token") or "")
        if not page_token:
            break
    return out


def _walk(native_mod: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    queue = ["0"]
    seen: set[str] = set()
    departments: list[dict[str, Any]] = []
    people_by_id: dict[str, dict[str, Any]] = {}
    while queue:
        did = queue.pop(0)
        if did in seen:
            continue
        seen.add(did)
        try:
            kids = _children(native_mod, did)
        except Exception as e:
            log.warning("org children fail dept=%s err=%s", did, e)
            continue
        for d in kids:
            oid = str(d.get("open_department_id") or d.get("department_id") or "").strip()
            if not oid:
                continue
            departments.append(
                {
                    "name": str(d.get("name") or "").strip(),
                    "open_department_id": oid,
                    "member_count": int(d.get("member_count") or 0),
                    "parent_department_id": str(d.get("parent_department_id") or ""),
                }
            )
            queue.append(oid)
        if did == "0":
            continue
        try:
            us = _users_of(native_mod, did)
        except Exception as e:
            log.warning("org users fail dept=%s err=%s", did, e)
            continue
        for u in us:
            oid = str(u.get("open_id") or "").strip()
            if not oid:
                continue
            # 不缓存手机号
            people_by_id[oid] = {
                "name": str(u.get("name") or "").strip(),
                "open_id": oid,
                "employee_no": str(u.get("employee_no") or "").strip(),
                "enterprise_email": str(u.get("enterprise_email") or "").strip(),
                "job_title": str(u.get("job_title") or "").strip(),
                "department_ids": list(u.get("department_ids") or []),
            }
    return departments, list(people_by_id.values())


def load_directory(*, force: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    now = time.time()
    with _LOCK:
        if (
            not force
            and _CACHE["people"]
            and _CACHE["at"]
            and now - float(_CACHE["at"]) < _TTL_SEC
        ):
            return list(_CACHE["departments"]), list(_CACHE["people"])
    from . import native as native_mod

    departments, people = _walk(native_mod)
    with _LOCK:
        _CACHE["at"] = time.time()
        _CACHE["departments"] = list(departments)
        _CACHE["people"] = list(people)
    log.info("org directory loaded depts=%s people=%s", len(departments), len(people))
    return departments, people


def _descendants(
    root_ids: set[str],
    departments: list[dict[str, Any]],
) -> set[str]:
    """root ∪ 全部子孙 open_department_id。"""
    kids: dict[str, list[str]] = {}
    for d in departments:
        pid = str(d.get("parent_department_id") or "").strip()
        oid = str(d.get("open_department_id") or "").strip()
        if pid and oid:
            kids.setdefault(pid, []).append(oid)
    out = set(root_ids)
    stack = list(root_ids)
    while stack:
        cur = stack.pop()
        for c in kids.get(cur) or []:
            if c not in out:
                out.add(c)
                stack.append(c)
    return out


def _known_org_labels() -> list[tuple[str, str]]:
    """已知部门名/业务队名/别名 → canonical。最长命中优先。"""
    pairs: list[tuple[str, str]] = []
    try:
        from ... import db, ingest

        for k, v in (db.team_alias_map() or {}).items():
            ks, vs = str(k or "").strip(), str(v or "").strip()
            if len(ks) >= 2 and vs:
                pairs.append((ks, vs))
        for t in ingest.TEAMS:
            ts = str(t or "").strip()
            if len(ts) >= 2:
                pairs.append((ts, ts))
    except Exception:
        pass
    try:
        from ..dept_team_map import load_dept_team_map

        for row in load_dept_team_map().get("departments") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            team = str(row.get("canonical_team") or "").strip()
            if len(name) >= 2:
                pairs.append((name, team or name))
            if len(team) >= 2:
                pairs.append((team, team))
    except Exception:
        pass
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for lab, canon in sorted(pairs, key=lambda x: len(x[0]), reverse=True):
        if lab in seen:
            continue
        seen.add(lab)
        out.append((lab, canon))
    return out


def _mesh_team_for_query(q: str) -> str | None:
    raw = (q or "").strip()
    if not raw:
        return None
    try:
        from ... import db

        n = db.normalize_team(raw)
        if n:
            return n
    except Exception:
        pass
    for lab, canon in _known_org_labels():
        if lab in raw:
            try:
                from ... import db

                return db.normalize_team(canon) or db.normalize_team(lab) or canon
            except Exception:
                return canon
    try:
        from ..dept_team_map import map_department_name

        return map_department_name(raw)
    except Exception:
        return None


def _dept_ids_for_mesh_team(team: str) -> set[str]:
    out: set[str] = set()
    try:
        from ..dept_team_map import load_dept_team_map

        for row in load_dept_team_map().get("departments") or []:
            if str(row.get("canonical_team") or "").strip() != team:
                continue
            oid = str(row.get("feishu_department_id") or "").strip()
            if oid:
                out.add(oid)
    except Exception:
        pass
    return out


def resolve_org_scope(
    query: str,
    departments: list[dict[str, Any]],
) -> tuple[set[str] | None, str]:
    """把队名/部门名解析成要列出的飞书部门 id 集合。

    返回 (dept_ids|None, label)。None 表示不是组织范围查询，应走人名检索。
    """
    q = (query or "").strip()
    if not q:
        return None, ""
    ql = q.lower()

    # 1) 命中飞书部门名（精确或互相包含）→ 该部门 + 子孙
    hit_roots: set[str] = set()
    hit_names: list[str] = []
    for d in departments:
        name = str(d.get("name") or "").strip()
        oid = str(d.get("open_department_id") or "").strip()
        if not name or not oid:
            continue
        nl = name.lower()
        if ql == nl or ql in nl or nl in ql:
            hit_roots.add(oid)
            hit_names.append(name)
    if hit_roots:
        ids = _descendants(hit_roots, departments)
        label = "、".join(hit_names[:4])
        if len(hit_names) > 4:
            label += "…"
        return ids, label

    # 2) Mesh 业务队（品牌创意 / 品牌创意团队 / 社群…）→ 映射表内全部飞书部门
    team = _mesh_team_for_query(q)
    if team:
        ids = _dept_ids_for_mesh_team(team)
        try:
            from ..dept_team_map import map_department_id, map_department_name

            parent_lookup = {
                str(d.get("open_department_id") or ""): str(d.get("parent_department_id") or "")
                for d in departments
            }
            for d in departments:
                oid = str(d.get("open_department_id") or "").strip()
                name = str(d.get("name") or "").strip()
                mapped = (
                    map_department_id(oid, parent_lookup=parent_lookup)
                    or map_department_name(name)
                )
                if mapped == team and oid:
                    ids.add(oid)
        except Exception:
            pass
        if ids:
            return ids, team
    return None, ""


def _people_in_depts(
    people: list[dict[str, Any]],
    dept_ids: set[str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for u in people:
        u_depts = {str(x).strip() for x in (u.get("department_ids") or []) if str(x).strip()}
        if not (u_depts & dept_ids):
            continue
        oid = str(u.get("open_id") or "").strip()
        key = oid or str(u.get("name") or "")
        if key in seen:
            continue
        seen.add(key)
        out.append(u)
    out.sort(key=lambda x: str(x.get("name") or ""))
    return out


def _roster_people_for_team(team: str) -> list[dict[str, Any]]:
    """飞书 walk 空结果时，用手填花名册按 Mesh 队兜底。"""
    try:
        from ..person_resolve import load_roster
        from ..dept_team_map import map_department_name
        from ... import db

        people = []
        for row in load_roster(force=False).get("people") or []:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            hit = False
            for t in row.get("teams") or []:
                ts = str(t or "").strip()
                mapped = map_department_name(ts) or db.normalize_team(ts)
                if mapped == team or ts == team:
                    hit = True
                    break
            if not hit:
                continue
            people.append(
                {
                    "name": name,
                    "open_id": str(row.get("open_id") or "").strip(),
                    "employee_no": str(row.get("employee_no") or "").strip(),
                    "enterprise_email": "",
                    "job_title": str(row.get("job_title") or "").strip(),
                    "department_ids": [],
                    "teams": list(row.get("teams") or []),
                    "source": "roster",
                }
            )
        people.sort(key=lambda x: x["name"])
        return people
    except Exception as e:
        log.info("roster team fallback fail: %s", e)
        return []


def member_names_for_scope(query: str, *, limit: int = 24) -> list[str]:
    """飞书部门/Mesh 队子树里的人名（问「我们团队」时给周报当线索）。"""
    q = (query or "").strip()
    if not q:
        return []
    try:
        departments, people = load_directory()
    except Exception as e:
        log.info("member_names_for_scope load fail: %s", e)
        departments, people = [], []
    scope_ids, label = resolve_org_scope(q, departments)
    matched: list[dict[str, Any]] = []
    if scope_ids:
        matched = _people_in_depts(people, scope_ids)
    if not matched:
        team = _mesh_team_for_query(q) or label
        if team:
            matched = _roster_people_for_team(team)
    names: list[str] = []
    for u in matched:
        n = str(u.get("name") or "").strip()
        if n and n not in names:
            names.append(n)
        if len(names) >= int(limit):
            break
    return names


def search_directory(
    query: str = "",
    *,
    max_results: int = 20,
    list_departments: bool = False,
    include_subdepartments: bool = True,
) -> ToolResultEnvelope:
    """按姓名/工号/邮箱查人；或按部门名/Mesh 业务队列成员。"""
    try:
        departments, people = load_directory()
    except Exception as e:
        return envelope_fail(f"org_directory_error:{e}"[:180], tool="feishu.search")

    q = (query or "").strip()
    ql = q.lower()
    limit = max(1, int(max_results))

    if list_departments or (not q and not people):
        items = []
        for d in departments:
            name = str(d.get("name") or "").strip()
            if ql and ql not in name.lower() and ql not in str(d.get("open_department_id") or "").lower():
                continue
            items.append(
                {
                    "title": name or d.get("open_department_id"),
                    "snippet": f"部门 · {d.get('member_count') or 0}人 · {d.get('open_department_id')}",
                    "docs_type": "department",
                    "id": d.get("open_department_id"),
                    "url": "",
                }
            )
            if len(items) >= limit:
                break
        return envelope_ok(
            normalize_docs(items, kind="department"),
            tool="feishu.search",
            max_results=limit,
        )

    # 部门 / 业务队范围 → 列成员（「品牌创意有谁」）
    scope_ids, scope_label = resolve_org_scope(q, departments)
    if scope_ids is not None:
        matched = _people_in_depts(people, scope_ids)
        source = "feishu_org"
        if not matched:
            team = _mesh_team_for_query(q) or scope_label
            matched = _roster_people_for_team(team) if team else []
            source = "roster" if matched else source
        team_limit = max(limit, 50)
        items = []
        if include_subdepartments or list_departments:
            child_depts = [
                d
                for d in departments
                if str(d.get("open_department_id") or "") in scope_ids
            ]
            child_depts.sort(key=lambda d: str(d.get("name") or ""))
            for d in child_depts[:40]:
                did = str(d.get("open_department_id") or "")
                n_here = sum(
                    1
                    for u in people
                    if did in {str(x) for x in (u.get("department_ids") or [])}
                )
                items.append(
                    {
                        "title": str(d.get("name") or did),
                        "snippet": f"子部门 · {n_here}人 · {did}",
                        "docs_type": "department",
                        "id": did,
                        "url": "",
                    }
                )
        for u in matched[:team_limit]:
            name = str(u.get("name") or "")
            job = str(u.get("job_title") or "")
            emp = str(u.get("employee_no") or "")
            oid = str(u.get("open_id") or "")
            teams = u.get("teams") or []
            u_depts = {str(x).strip() for x in (u.get("department_ids") or []) if str(x).strip()}
            dept_names = [
                str(d.get("name") or "")
                for d in departments
                if str(d.get("open_department_id") or "") in u_depts
            ]
            bits = [p for p in (scope_label, (dept_names[0] if dept_names else ""), job, emp) if p]
            if teams:
                bits.append("/".join(str(t) for t in teams[:3]))
            items.append(
                {
                    "title": name or oid or "同事",
                    "snippet": " · ".join(bits) or source,
                    "docs_type": "user",
                    "id": oid,
                    "url": "",
                }
            )
        return envelope_ok(
            normalize_docs(items, kind="user"),
            tool="feishu.search",
            max_results=team_limit,
        )

    items = []
    for u in people:
        name = str(u.get("name") or "")
        emp = str(u.get("employee_no") or "")
        email = str(u.get("enterprise_email") or "")
        oid = str(u.get("open_id") or "")
        job = str(u.get("job_title") or "")
        if ql and not (
            ql in name.lower()
            or ql in emp.lower()
            or ql in email.lower()
            or ql in oid.lower()
            or ql in job.lower()
        ):
            continue
        parts = [p for p in (job, emp, email) if p]
        items.append(
            {
                "title": name or oid or "同事",
                "snippet": " · ".join(parts),
                "docs_type": "user",
                "id": oid,
                "url": "",
            }
        )
        if len(items) >= limit:
            break
    return envelope_ok(
        normalize_docs(items, kind="user"),
        tool="feishu.search",
        max_results=limit,
    )
