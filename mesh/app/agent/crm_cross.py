"""CRM × 已上线周报交叉（both / crm_only / weekly_only）。

CRM 侧：people / companies（+ aliases）
周报侧：entity_team_facts.name（复用 _entity_keys 对齐）
匹配不上的进 pending 桶，不硬并。

both / crm_only 样例会附带 CRM 明细（headline / 最近沟通 / Take），
便于 mouth 写成「重叠名单 + 节点进展」，而不是只吐对照对。
"""
from __future__ import annotations

import re
from typing import Any

from .. import qa_structured
from .crm_search import _owner_label

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


def _person_crm_detail(con, name: str) -> dict[str, Any]:
    """对照名单上的人：补 CRM 侧进展，供成文引用（不是冒充总数）。"""
    name = (name or "").strip()
    if not name:
        return {}
    row = con.execute(
        """
        SELECT display_name, company_names, headline, last_touched
        FROM crm_people
        WHERE display_name = ? OR aliases LIKE ?
        ORDER BY CASE WHEN display_name = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (name, f"%{name}%", name),
    ).fetchone()
    detail: dict[str, Any] = {}
    if row:
        detail = {
            "name": row["display_name"] or name,
            "company": row["company_names"] or "",
            "headline": row["headline"] or "",
            "last_touched": row["last_touched"] or "",
        }
    ix = con.execute(
        """
        SELECT title, date_start, interact_type, our_side
        FROM crm_interactions
        WHERE people_names LIKE ?
        ORDER BY date_start DESC, title DESC
        LIMIT 1
        """,
        (f"%{name}%",),
    ).fetchone()
    if ix:
        detail["last_interaction"] = {
            "date": ix["date_start"] or "",
            "type": ix["interact_type"] or "",
            "title": ix["title"] or "",
            "our_side": _owner_label(ix["our_side"] or ""),
        }
    take = con.execute(
        """
        SELECT verdict, scenario, owner, last_reviewed
        FROM crm_takes
        WHERE (person_names LIKE ? OR name LIKE ?)
          AND verdict IS NOT NULL AND TRIM(verdict) != ''
        ORDER BY last_reviewed DESC, name
        LIMIT 1
        """,
        (f"%{name}%", f"%{name}%"),
    ).fetchone()
    if take:
        detail["take"] = {
            "verdict": (take["verdict"] or "")[:160],
            "scenario": (take["scenario"] or "")[:80],
            "owner": _owner_label(take["owner"] or "Lilyann"),
            "last_reviewed": take["last_reviewed"] or "",
        }
    return detail


def _company_crm_detail(con, name: str) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {}
    row = con.execute(
        """
        SELECT name, one_liner, sector, stage
        FROM crm_companies
        WHERE name = ? OR aliases LIKE ?
        ORDER BY CASE WHEN name = ? THEN 0 ELSE 1 END
        LIMIT 1
        """,
        (name, f"%{name}%", name),
    ).fetchone()
    if not row:
        return {}
    return {
        "name": row["name"] or name,
        "one_liner": row["one_liner"] or "",
        "sector": row["sector"] or "",
        "stage": row["stage"] or "",
    }


def _weekly_detail(
    con,
    name: str,
    *,
    date_from: str | None,
    date_to: str | None,
    weekly_team: str = "",
) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        return {}
    where = ["name = ?"]
    params: list[Any] = [name]
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
    row = con.execute(
        f"""
        SELECT team, section, snippet, group_title, date_end, issue_slug
        FROM entity_team_facts
        WHERE {' AND '.join(where)}
        ORDER BY date_end DESC, id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if not row:
        return {}
    return {
        "team": row["team"] or "",
        "section": row["section"] or "",
        "snippet": (row["snippet"] or "")[:200],
        "group_title": row["group_title"] or "",
        "date_end": row["date_end"] or "",
        "issue_slug": row["issue_slug"] or "",
    }


def _format_crm_detail_line(detail: dict[str, Any], *, kind: str) -> str:
    if not detail:
        return ""
    bits: list[str] = []
    if kind == "person":
        if detail.get("company"):
            bits.append(str(detail["company"]))
        if detail.get("headline"):
            bits.append(str(detail["headline"])[:120])
        ix = detail.get("last_interaction") or {}
        if isinstance(ix, dict) and (ix.get("date") or ix.get("title")):
            piece = f"最近沟通 {ix.get('date') or ''} {ix.get('type') or ''}".strip()
            if ix.get("title"):
                piece += f"「{ix['title']}」"
            if ix.get("our_side"):
                piece += f"（我方：{ix['our_side']}）"
            bits.append(piece.strip())
        take = detail.get("take") or {}
        if isinstance(take, dict) and take.get("verdict"):
            bits.append(f"Take：{take['verdict']}")
    else:
        if detail.get("one_liner"):
            bits.append(str(detail["one_liner"])[:120])
        if detail.get("sector"):
            bits.append(str(detail["sector"]))
        if detail.get("stage"):
            bits.append(str(detail["stage"]))
    return "；".join(b for b in bits if b)


def _format_weekly_detail_line(detail: dict[str, Any]) -> str:
    if not detail:
        return ""
    bits: list[str] = []
    if detail.get("team"):
        bits.append(str(detail["team"]))
    if detail.get("section"):
        bits.append(str(detail["section"]))
    if detail.get("date_end"):
        bits.append(str(detail["date_end"]))
    if detail.get("snippet"):
        bits.append(str(detail["snippet"])[:120])
    elif detail.get("group_title"):
        bits.append(str(detail["group_title"])[:80])
    return " · ".join(bits)


def _enrich_rows(
    con,
    rows: list[dict],
    *,
    op: str,
    kind: str,
    date_from: str | None,
    date_to: str | None,
    weekly_team: str,
) -> list[dict]:
    enriched: list[dict] = []
    for r in rows:
        item = dict(r)
        crm_name = str(item.get("crm") or item.get("name") or "").strip()
        if op in ("both", "crm_only") and crm_name:
            if kind == "company":
                item["crm_detail"] = _company_crm_detail(con, crm_name)
            else:
                item["crm_detail"] = _person_crm_detail(con, crm_name)
        if op == "both":
            weekly_name = str(item.get("weekly") or "").strip()
            if weekly_name:
                item["weekly_detail"] = _weekly_detail(
                    con,
                    weekly_name,
                    date_from=date_from,
                    date_to=date_to,
                    weekly_team=weekly_team,
                )
        elif op == "weekly_only":
            weekly_name = str(item.get("name") or "").strip()
            if weekly_name:
                item["weekly_detail"] = _weekly_detail(
                    con,
                    weekly_name,
                    date_from=date_from,
                    date_to=date_to,
                    weekly_team=weekly_team,
                )
        enriched.append(item)
    return enriched


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

    rows = _enrich_rows(
        con,
        rows,
        op=op,
        kind=kind,
        date_from=date_from,
        date_to=date_to,
        weekly_team=weekly_team or "",
    )

    lines = [
        "【硅谷 CRM × 已上线周报 · 交叉】",
        "说明：CRM 与周报口径不同（思琪侧人脉库 vs 国内各队周报事实），分栏对照，不合成一个假总数。",
        "成文提示：下列样例含 CRM 进展明细；请按节点写重叠结论，勿只复读「CRM X ↔ 周报 Y」。",
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
        lines.append(f"## 样例（最多 {len(rows)} 条，不是总数；含 CRM 明细）")
        for r in rows:
            if op == "both":
                lines.append(f"- CRM **{r.get('crm')}** ↔ 周报 **{r.get('weekly')}**")
            else:
                side = "CRM" if op == "crm_only" else "周报"
                lines.append(f"- [{side}] **{r.get('name')}**")
            crm_line = _format_crm_detail_line(
                r.get("crm_detail") or {}, kind=kind
            )
            if crm_line:
                lines.append(f"  · CRM：{crm_line}")
            weekly_line = _format_weekly_detail_line(r.get("weekly_detail") or {})
            if weekly_line:
                lines.append(f"  · 周报：{weekly_line}")
    else:
        lines.append("")
        lines.append("本运算下没有命中。")

    items = []
    for r in rows[:12]:
        if op == "both":
            snip = _format_crm_detail_line(r.get("crm_detail") or {}, kind=kind)
            items.append(
                {
                    "title": r.get("crm") or r.get("weekly"),
                    "snippet": snip or f"weekly={r.get('weekly')}",
                    "kind": f"cross:{op}",
                }
            )
        else:
            if op == "crm_only":
                snip = _format_crm_detail_line(r.get("crm_detail") or {}, kind=kind)
            else:
                snip = _format_weekly_detail_line(r.get("weekly_detail") or {})
            items.append(
                {
                    "title": r.get("name"),
                    "snippet": snip or op,
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
