"""公司通讯录（应用身份 / tenant_access_token）。

飞书 bot 可以用通讯录 OpenAPI 按部门树拉人；不是「做不到」，
此前误把 CLI `contact +search-user`（仅 user）当成唯一入口。

可见范围 = 应用在飞书后台的「通讯录权限范围」；本租户实测可覆盖部门树下全员。
短时进程内缓存，避免每次聊天都全量 walk。
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


def search_directory(
    query: str = "",
    *,
    max_results: int = 20,
    list_departments: bool = False,
) -> ToolResultEnvelope:
    """按姓名/工号/邮箱子串查人；空 query 且 list_departments 则列部门。"""
    try:
        departments, people = load_directory()
    except Exception as e:
        return envelope_fail(f"org_directory_error:{e}"[:180], tool="feishu.search")

    q = (query or "").strip().lower()
    if list_departments or (not q and not people):
        items = []
        for d in departments:
            name = str(d.get("name") or "").strip()
            if q and q not in name.lower() and q not in str(d.get("open_department_id") or "").lower():
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
            if len(items) >= int(max_results):
                break
        return envelope_ok(normalize_docs(items, kind="department"), tool="feishu.search")

    items = []
    for u in people:
        name = str(u.get("name") or "")
        emp = str(u.get("employee_no") or "")
        email = str(u.get("enterprise_email") or "")
        oid = str(u.get("open_id") or "")
        job = str(u.get("job_title") or "")
        if q and not (
            q in name.lower()
            or q in emp.lower()
            or q in email.lower()
            or q in oid.lower()
            or q in job.lower()
        ):
            continue
        parts = [p for p in (job, emp, email, oid) if p]
        items.append(
            {
                "title": name or oid or "同事",
                "snippet": " · ".join(parts),
                "docs_type": "user",
                "id": oid,
                "url": "",
            }
        )
        if len(items) >= int(max_results):
            break
    return envelope_ok(normalize_docs(items, kind="user"), tool="feishu.search")
