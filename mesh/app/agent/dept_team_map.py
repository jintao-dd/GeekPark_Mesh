"""飞书 Department → Mesh canonical_team 映射。

权威树在飞书；Mesh 只维护本映射（见 docs/AGENT_ARCHITECTURE_V1.md §3）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("uvicorn.error")

_MAP_PATH = Path(__file__).resolve().parent / "data" / "feishu_department_team_map.json"
_CACHE: dict[str, Any] | None = None


def load_dept_team_map(*, force: bool = False) -> dict[str, Any]:
    global _CACHE
    if _CACHE is not None and not force:
        return _CACHE
    data: dict[str, Any] = {"departments": [], "by_id": {}, "by_name": {}}
    try:
        if _MAP_PATH.is_file():
            raw = json.loads(_MAP_PATH.read_text(encoding="utf-8"))
            rows = raw.get("departments") if isinstance(raw, dict) else []
            if not isinstance(rows, list):
                rows = []
            by_id: dict[str, dict[str, Any]] = {}
            by_name: dict[str, dict[str, Any]] = {}
            for row in rows:
                if not isinstance(row, dict):
                    continue
                oid = str(row.get("feishu_department_id") or "").strip()
                name = str(row.get("name") or "").strip()
                if oid:
                    by_id[oid] = row
                if name and name not in by_name:
                    by_name[name] = row
            data = {
                "departments": rows,
                "by_id": by_id,
                "by_name": by_name,
                "path": str(_MAP_PATH),
            }
    except Exception as e:
        log.warning("dept_team_map load failed: %s", e)
    _CACHE = data
    return data


def feishu_name_aliases() -> dict[str, str]:
    """飞书部门名 → Mesh canonical_team（供 normalize_team 别名表）。"""
    out: dict[str, str] = {}
    for row in load_dept_team_map().get("departments") or []:
        name = str(row.get("name") or "").strip()
        team = str(row.get("canonical_team") or "").strip()
        if name and team:
            out[name] = team
    return out


def map_department_id(
    department_id: str,
    *,
    parent_lookup: dict[str, str] | None = None,
) -> str | None:
    """部门 open_id → canonical_team；未直接配置则沿 parent 上溯。"""
    oid = (department_id or "").strip()
    if not oid or oid == "0":
        return None
    data = load_dept_team_map()
    by_id: dict[str, dict[str, Any]] = data.get("by_id") or {}
    seen: set[str] = set()
    cur = oid
    while cur and cur not in seen and cur != "0":
        seen.add(cur)
        row = by_id.get(cur)
        if row:
            team = str(row.get("canonical_team") or "").strip()
            if team:
                return team
            parent = str(row.get("parent_department_id") or "").strip()
        else:
            parent = ""
        if parent_lookup and not parent:
            parent = str(parent_lookup.get(cur) or "").strip()
        cur = parent
    return None


def map_department_name(name: str) -> str | None:
    n = (name or "").strip()
    if not n:
        return None
    row = (load_dept_team_map().get("by_name") or {}).get(n)
    if not row:
        return None
    team = str(row.get("canonical_team") or "").strip()
    return team or None


def map_department_ids(
    department_ids: list[str] | None,
    *,
    parent_lookup: dict[str, str] | None = None,
) -> list[str]:
    """多部门 → 去重后的 Mesh 业务队列表（保序）。"""
    out: list[str] = []
    for did in department_ids or []:
        team = map_department_id(str(did), parent_lookup=parent_lookup)
        if team and team not in out:
            out.append(team)
    return out


def mapped_teams_for_open_id(open_id: str) -> list[str]:
    """按通讯录用户的 department_ids 解析 Mesh 队（失败返回空）。"""
    oid = (open_id or "").strip()
    if not oid:
        return []
    try:
        from .feishu_hands import org_directory

        depts, people = org_directory.load_directory()
    except Exception as e:
        log.info("dept_team_map org unavailable: %s", e)
        return []
    parent_lookup = {
        str(d.get("open_department_id") or ""): str(d.get("parent_department_id") or "")
        for d in depts or []
        if str(d.get("open_department_id") or "").strip()
    }
    for u in people or []:
        if not isinstance(u, dict):
            continue
        if str(u.get("open_id") or "").strip() != oid:
            continue
        ids = [str(x).strip() for x in (u.get("department_ids") or []) if str(x).strip()]
        return map_department_ids(ids, parent_lookup=parent_lookup)
    return []


def brand_creative_tree_note() -> str:
    """Ontology 用：品牌创意飞书子树说明。"""
    kids = []
    for row in load_dept_team_map().get("departments") or []:
        if str(row.get("parent_department_id") or "") == "od-3f2b9310260ff7673d0694fb43060515":
            kids.append(str(row.get("name") or ""))
    kids = [k for k in kids if k]
    if not kids:
        return "品牌创意部 → Mesh「品牌创意团队」"
    return "飞书「品牌创意部」含子组 " + "、".join(kids) + " → 均映射 Mesh「品牌创意团队」"
