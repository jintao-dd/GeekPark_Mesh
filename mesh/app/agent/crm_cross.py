"""CRM × 已上线周报交叉（both / crm_only / weekly_only）。

CRM 侧：people / companies（+ aliases）
周报侧：entity_team_facts.name（复用 _entity_keys 对齐）
匹配不上的进 pending 桶，不硬并。
"""
from __future__ import annotations

import re
from typing import Any

from .. import qa_structured

_CROSS_OPS = frozenset({"both", "crm_only", "weekly_only"})


def infer_cross_op(q: str) -> str:
    q = q or ""
    if re.search(r"只有CRM|CRM有但|人脉库有但|CRM.*没有.*周报|周报.*没有", q, re.I):
        if re.search(r"周报有但|只有周报|周报.*没有.*CRM", q, re.I):
            return "weekly_only"
        return "crm_only"
    if re.search(r"只有周报|周报有但|周报.*CRM.*没有", q, re.I):
        return "weekly_only"
    if re.search(r"两边|都出现|交叉|同时|重合|交集", q):
        return "both"
    return "both"


def infer_entity_kind(q: str) -> str:
    if re.search(r"公司|企业", q or ""):
        return "company"
    return "person"


def _norm_keys(name: str) -> set[str]:
    keys = set(qa_structured._entity_keys(name) or set())
    n = qa_structured._entity_norm(name)
    if n:
        keys.add(n)
    return {k for k in keys if k and len(k) >= 2}


def _crm_people_index(con) -> tuple[dict[str, str], list[str]]:
    """key -> display_name；并返回全部展示名。"""
    key_to_name: dict[str, str] = {}
    names: list[str] = []
    for r in con.execute(
        "SELECT display_name, aliases FROM crm_people"
    ).fetchall():
        disp = (r["display_name"] or "").strip()
        if not disp:
            continue
        names.append(disp)
        blob = f"{disp} {r['aliases'] or ''}"
        for k in _norm_keys(blob):
            key_to_name.setdefault(k, disp)
        for k in _norm_keys(disp):
            key_to_name.setdefault(k, disp)
    return key_to_name, names


def _crm_company_index(con) -> tuple[dict[str, str], list[str]]:
    key_to_name: dict[str, str] = {}
    names: list[str] = []
    for r in con.execute("SELECT name, aliases FROM crm_companies").fetchall():
        disp = (r["name"] or "").strip()
        if not disp:
            continue
        names.append(disp)
        blob = f"{disp} {r['aliases'] or ''}"
        for k in _norm_keys(blob):
            key_to_name.setdefault(k, disp)
        for k in _norm_keys(disp):
            key_to_name.setdefault(k, disp)
    return key_to_name, names


def _weekly_entities(
    con,
    *,
    entity_kind: str,
    date_from: str | None,
    date_to: str | None,
    weekly_team: str = "",
) -> tuple[dict[str, str], list[str]]:
    """key -> original name from entity_team_facts。"""
    where = ["1=1"]
    params: list[Any] = []
    if date_from:
        where.append("date_end IS NOT NULL AND date_end >= ?")
        params.append(date_from)
    if date_to:
        where.append("date_end IS NOT NULL AND date_end <= ?")
        params.append(date_to)
    team = (weekly_team or "").strip()
    if team:
        from .. import db as mesh_db

        nt = mesh_db.normalize_team(team) or team
        where.append("team = ?")
        params.append(nt)
    # kind 在生产里常不是 person；对人/公司都拉，再用名字启发式不强滤
    sql = f"SELECT DISTINCT name, team, section FROM entity_team_facts WHERE {' AND '.join(where)}"
    key_to_name: dict[str, str] = {}
    names: list[str] = []
    for r in con.execute(sql, params).fetchall():
        name = (r["name"] or "").strip()
        if not name or len(name) < 2:
            continue
        names.append(name)
        for k in _norm_keys(name):
            key_to_name.setdefault(k, name)
    return key_to_name, names


def _match_sets(
    crm_keys: dict[str, str],
    weekly_keys: dict[str, str],
) -> tuple[list[dict], list[dict], list[dict], list[str]]:
    """返回 both / crm_only / weekly_only 展示项 + pending 说明。"""
    crm_matched: set[str] = set()
    weekly_matched: set[str] = set()
    both: list[dict] = []
    pending: list[str] = []

    # 按 CRM 展示名聚合
    crm_by_disp: dict[str, set[str]] = {}
    for k, disp in crm_keys.items():
        crm_by_disp.setdefault(disp, set()).add(k)

    weekly_by_disp: dict[str, set[str]] = {}
    for k, disp in weekly_keys.items():
        weekly_by_disp.setdefault(disp, set()).add(k)

    for disp, keys in crm_by_disp.items():
        hit_weekly = None
        for k in keys:
            if k in weekly_keys:
                hit_weekly = weekly_keys[k]
                crm_matched.add(disp)
                weekly_matched.add(hit_weekly)
                break
        if hit_weekly:
            both.append({"crm": disp, "weekly": hit_weekly})

    crm_only = [{"name": d} for d in sorted(crm_by_disp) if d not in crm_matched]
    weekly_only = [
        {"name": d} for d in sorted(weekly_by_disp) if d not in weekly_matched
    ]

    # 弱提示：两侧都有短名但未键对齐
    if len(crm_only) > 50 and len(weekly_only) > 50:
        pending.append(
            "两侧未对齐条目较多；别名不全时会出现假阴性，已放入待核对而非强行合并。"
        )
    return both, crm_only, weekly_only, pending


def cross_crm_weekly(
    con,
    *,
    op: str = "both",
    entity_kind: str = "person",
    date_from: str | None = None,
    date_to: str | None = None,
    weekly_team: str = "",
    query: str = "",
    limit: int = 30,
) -> dict[str, Any]:
    op = (op or "both").strip().lower()
    if op not in _CROSS_OPS:
        op = "both"
    kind = (entity_kind or "person").strip().lower()
    if kind not in ("person", "company"):
        kind = "person"

    if kind == "company":
        crm_keys, _ = _crm_company_index(con)
    else:
        crm_keys, _ = _crm_people_index(con)
    weekly_keys, _ = _weekly_entities(
        con,
        entity_kind=kind,
        date_from=date_from,
        date_to=date_to,
        weekly_team=weekly_team,
    )
    both, crm_only, weekly_only, pending = _match_sets(crm_keys, weekly_keys)

    if op == "both":
        rows = both[:limit]
        total = len(both)
        label = "两边都有"
    elif op == "crm_only":
        rows = crm_only[:limit]
        total = len(crm_only)
        label = "仅 CRM 有"
    else:
        rows = weekly_only[:limit]
        total = len(weekly_only)
        label = "仅周报有"

    lines = [
        "【硅谷 CRM × 已上线周报 · 交叉】",
        "说明：CRM 与周报口径不同（思琪侧人脉库 vs 国内各队周报事实），分栏对照，不合成一个假总数。",
        f"运算：{label}（{op}）",
        f"实体类型：{kind}",
        f"命中：**{total}**",
    ]
    if query:
        lines.append(f"查询：{query}")
    if date_from or date_to:
        lines.append(f"时间窗（周报 date_end / CRM 不限除非另有沟通窗）：{date_from or '…'} → {date_to or '至今'}")
    if weekly_team:
        lines.append(f"周报侧队过滤：{weekly_team}")
    lines.append(
        f"集合规模：两边都有={len(both)}；仅CRM={len(crm_only)}；仅周报={len(weekly_only)}"
    )
    for p in pending:
        lines.append(f"注意：{p}")
    if rows:
        lines.append("")
        lines.append(f"## 样例（最多 {len(rows)} 条，不是总数）")
        for r in rows:
            if op == "both":
                lines.append(f"- CRM **{r.get('crm')}** ↔ 周报 **{r.get('weekly')}**")
            else:
                side = "CRM" if op == "crm_only" else "周报"
                lines.append(f"- [{side}] **{r.get('name')}**")
    else:
        lines.append("")
        lines.append("本运算下没有命中。")

    items = []
    for r in rows[:12]:
        if op == "both":
            items.append(
                {
                    "title": r.get("crm") or r.get("weekly"),
                    "snippet": f"weekly={r.get('weekly')}",
                    "kind": f"cross:{op}",
                }
            )
        else:
            items.append(
                {
                    "title": r.get("name"),
                    "snippet": op,
                    "kind": f"cross:{op}",
                }
            )

    return {
        "ok": True,
        "empty": total <= 0,
        "mode": "cross",
        "cross_op": op,
        "entity_kind": kind,
        "total": total,
        "counts": {
            "both": len(both),
            "crm_only": len(crm_only),
            "weekly_only": len(weekly_only),
        },
        "rows": rows,
        "pending": pending,
        "query": query,
        "text": "\n".join(lines),
        "source_tier": "crm_prior",
        "owner_line": "思琪（Lilyann）· 硅谷 CRM × 已上线周报",
        "items": items,
        "n_hits": total,
        "crm_metric": "",
        "cross_op_obs": op,
    }
