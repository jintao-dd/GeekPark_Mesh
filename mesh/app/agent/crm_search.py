"""Notion CRM 检索（硅谷/思琪·Lilyann 对外沟通底盘）。

People 为枢纽；Interactions=事件；Takes=判断。
不进 Published Ask 主链；source_tier=crm_prior。
永不返回 email / wechat。
"""
from __future__ import annotations

import re
from typing import Any

from .. import db
from .models import ClaimBinding, ToolResult

# CRM 字段里的对外名 → 对内称呼
_OWNER_DISPLAY = {
    "lilyann": "思琪（Lilyann）",
    "lilyanna": "思琪（Lilyann）",
    "sean shen": "Sean Shen",
}

_SIQI_ALIASES = frozenset({"思琪", "赵思琪", "lilyann", "lilyanna", "siqi"})


def _owner_label(raw: str) -> str:
    s = (raw or "").strip()
    if not s:
        return "思琪侧"
    parts = []
    for p in re.split(r"[,，、/]", s):
        p = p.strip()
        if not p:
            continue
        parts.append(_OWNER_DISPLAY.get(p.lower(), p))
    return "、".join(parts) if parts else "思琪侧"


def _like(q: str) -> str:
    return f"%{(q or '').strip()}%"


def _table_ready(con) -> bool:
    try:
        n = con.execute("SELECT COUNT(*) AS c FROM crm_people").fetchone()["c"]
        return int(n or 0) > 0
    except Exception:
        try:
            con.rollback()
        except Exception:
            pass
        return False


def _search_people(con, q: str, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT display_name, company_names, headline, sector, location,
               last_touched, interaction_count
        FROM crm_people
        WHERE display_name LIKE ? OR aliases LIKE ? OR company_names LIKE ?
              OR headline LIKE ?
        ORDER BY
          CASE WHEN display_name LIKE ? THEN 0 ELSE 1 END,
          CASE WHEN last_touched IS NOT NULL AND TRIM(last_touched) != '' THEN 0 ELSE 1 END,
          display_name
        LIMIT ?
        """,
        (_like(q), _like(q), _like(q), _like(q), _like(q), int(limit)),
    ).fetchall()
    out = []
    for r in rows:
        out.append(
            {
                "name": r["display_name"] or "",
                "company": r["company_names"] or "",
                "headline": r["headline"] or "",
                "sector": r["sector"] or "",
                "location": r["location"] or "",
                "last_touched": r["last_touched"] or "",
            }
        )
    return out


def _search_companies(con, q: str, *, limit: int = 8) -> list[dict[str, Any]]:
    rows = con.execute(
        """
        SELECT name, one_liner, sector, stage, website
        FROM crm_companies
        WHERE name LIKE ? OR aliases LIKE ? OR one_liner LIKE ? OR sector LIKE ?
        ORDER BY CASE WHEN name LIKE ? THEN 0 ELSE 1 END, name
        LIMIT ?
        """,
        (_like(q), _like(q), _like(q), _like(q), _like(q), int(limit)),
    ).fetchall()
    return [
        {
            "name": r["name"] or "",
            "one_liner": r["one_liner"] or "",
            "sector": r["sector"] or "",
            "stage": r["stage"] or "",
            "website": r["website"] or "",
        }
        for r in rows
    ]


def _search_interactions(
    con,
    q: str = "",
    *,
    recent: bool = False,
    limit: int = 12,
) -> list[dict[str, Any]]:
    if recent or not (q or "").strip():
        rows = con.execute(
            """
            SELECT title, date_start, interact_type, people_names, our_side, output_link
            FROM crm_interactions
            ORDER BY date_start DESC, title DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT title, date_start, interact_type, people_names, our_side, output_link
            FROM crm_interactions
            WHERE title LIKE ? OR people_names LIKE ? OR interact_type LIKE ?
                  OR our_side LIKE ?
            ORDER BY date_start DESC
            LIMIT ?
            """,
            (_like(q), _like(q), _like(q), _like(q), int(limit)),
        ).fetchall()
    return [
        {
            "date": r["date_start"] or "",
            "type": r["interact_type"] or "",
            "people": r["people_names"] or "",
            "title": r["title"] or "",
            "our_side": _owner_label(r["our_side"] or ""),
            "output_link": r["output_link"] or "",
        }
        for r in rows
    ]


def _search_takes(con, q: str = "", *, limit: int = 10) -> list[dict[str, Any]]:
    if not (q or "").strip() or (q or "").strip().lower() in _SIQI_ALIASES:
        rows = con.execute(
            """
            SELECT name, person_names, verdict, scenario, owner, last_reviewed, is_prospect
            FROM crm_takes
            WHERE verdict IS NOT NULL AND TRIM(verdict) != ''
            ORDER BY last_reviewed DESC, name
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    else:
        rows = con.execute(
            """
            SELECT name, person_names, verdict, scenario, owner, last_reviewed, is_prospect
            FROM crm_takes
            WHERE person_names LIKE ? OR name LIKE ? OR verdict LIKE ? OR scenario LIKE ?
            ORDER BY last_reviewed DESC, name
            LIMIT ?
            """,
            (_like(q), _like(q), _like(q), _like(q), int(limit)),
        ).fetchall()
    return [
        {
            "person": r["person_names"] or "",
            "name": r["name"] or "",
            "verdict": r["verdict"] or "",
            "scenario": r["scenario"] or "",
            "owner": _owner_label(r["owner"] or "Lilyann"),
            "last_reviewed": r["last_reviewed"] or "",
            "is_prospect": r["is_prospect"] or "",
        }
        for r in rows
    ]


def _infer_mode(q: str, mode: str) -> str:
    m = (mode or "").strip().lower()
    if m in ("person", "company", "recent", "take"):
        return m
    return "auto"


def _format_text(
    *,
    mode: str,
    query: str,
    people: list[dict],
    companies: list[dict],
    interactions: list[dict],
    takes: list[dict],
) -> str:
    lines = [
        "【硅谷 CRM · 思琪（Lilyann）侧跟进】",
        "说明：来自 Notion CRM 底库，不是本期已上线周报。",
    ]
    if query:
        lines.append(f"查询：{query}")

    if takes:
        lines.append("")
        lines.append("## 判断 / 下一步（Takes）")
        for t in takes:
            who = t["person"] or t["name"] or "（未挂人）"
            bit = f"- **{who}**"
            if t["last_reviewed"]:
                bit += f"（审阅 {t['last_reviewed']}）"
            bit += f"：{t['verdict']}"
            if t["scenario"]:
                bit += f"｜场景：{t['scenario']}"
            lines.append(bit)

    if interactions:
        lines.append("")
        lines.append("## 沟通时间线（Interactions）")
        for ix in interactions:
            bit = f"- {ix['date'] or '?'} · {ix['type'] or '沟通'}"
            if ix["people"]:
                bit += f" · {ix['people']}"
            if ix["title"] and ix["title"] not in bit:
                bit += f" — {ix['title']}"
            if ix["our_side"]:
                bit += f"（我方：{ix['our_side']}）"
            lines.append(bit)

    if people and mode in ("person", "auto"):
        lines.append("")
        lines.append("## 人脉档案（People）")
        for p in people:
            bit = f"- **{p['name']}**"
            if p["company"]:
                bit += f" @ {p['company']}"
            if p["headline"]:
                bit += f" — {p['headline']}"
            lines.append(bit)

    if companies:
        lines.append("")
        lines.append("## 公司（Companies）")
        for c in companies:
            bit = f"- **{c['name']}**"
            if c["sector"]:
                bit += f"｜{c['sector']}"
            if c["one_liner"]:
                bit += f" — {c['one_liner']}"
            lines.append(bit)

    if not (takes or interactions or people or companies):
        lines.append("")
        lines.append("这轮 CRM 里没有匹配到人/沟通/判断。")
    return "\n".join(lines)


def search_crm(
    con,
    *,
    query: str = "",
    mode: str = "auto",
    limit: int = 10,
) -> dict[str, Any]:
    """返回 payload dict（供 Tool 包装）。"""
    q = (query or "").strip()
    if not _table_ready(con):
        return {
            "ok": False,
            "empty": True,
            "error": "crm_empty",
            "text": "硅谷 CRM 底库还是空的（尚未从 Notion 同步）。",
            "source_tier": "crm_prior",
            "items": [],
        }

    m = _infer_mode(q, mode)
    people: list[dict] = []
    companies: list[dict] = []
    interactions: list[dict] = []
    takes: list[dict] = []

    if m == "recent" or (m == "auto" and not q):
        interactions = _search_interactions(con, q, recent=True, limit=limit)
        names = []
        for ix in interactions:
            for p in (ix.get("people") or "").split(","):
                p = p.strip()
                if p and p not in names:
                    names.append(p)
        for name in names[:8]:
            takes.extend(_search_takes(con, name, limit=2))
        seen = set()
        uniq = []
        for t in takes:
            k = (t.get("person"), t.get("verdict"))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        takes = uniq[:limit]
    elif m == "take":
        takes = _search_takes(con, q, limit=limit)
        if q:
            interactions = _search_interactions(con, q, limit=min(6, limit))
            people = _search_people(con, q, limit=3)
    elif m == "company":
        companies = _search_companies(con, q, limit=limit)
    elif m == "person":
        people = _search_people(con, q, limit=limit) if q else []
        takes = _search_takes(con, q, limit=limit) if q else _search_takes(con, "", limit=5)
        interactions = (
            _search_interactions(con, q, limit=min(8, limit))
            if q
            else _search_interactions(con, "", recent=True, limit=5)
        )
        if not people and not takes and not interactions and q:
            companies = _search_companies(con, q, limit=5)
    else:  # auto + query：四面都搜，空则退回最近沟通
        people = _search_people(con, q, limit=limit)
        takes = _search_takes(con, q, limit=limit)
        interactions = _search_interactions(con, q, limit=min(8, limit))
        companies = _search_companies(con, q, limit=5)
        if not (people or takes or interactions or companies):
            interactions = _search_interactions(con, "", recent=True, limit=limit)

    text = _format_text(
        mode=m,
        query=q,
        people=people,
        companies=companies,
        interactions=interactions,
        takes=takes,
    )
    items = []
    for t in takes:
        items.append({"title": t.get("person") or t.get("name"), "snippet": t.get("verdict"), "kind": "take"})
    for ix in interactions:
        items.append({"title": ix.get("title") or ix.get("people"), "snippet": f"{ix.get('date')} {ix.get('type')}", "kind": "interaction"})
    for p in people:
        items.append({"title": p.get("name"), "snippet": p.get("company") or p.get("headline"), "kind": "person"})

    return {
        "ok": True,
        "empty": not bool(items),
        "mode": m,
        "query": q,
        "text": text,
        "source_tier": "crm_prior",
        "owner_line": "思琪（Lilyann）· 硅谷 CRM",
        "people": people,
        "companies": companies,
        "interactions": interactions,
        "takes": takes,
        "items": items,
        "n_hits": len(items),
    }


def tool_crm_search(
    con,
    identity: Any,
    permission: Any,
    context: Any,
    args: dict[str, Any] | None = None,
) -> ToolResult:
    args = args or {}
    q = str(args.get("query") or args.get("q") or args.get("keyword") or "").strip()
    mode = str(args.get("mode") or "auto").strip()
    limit = int(args.get("limit") or args.get("max_results") or 10)
    payload = search_crm(con, query=q, mode=mode, limit=limit)
    ok = bool(payload.get("ok")) and not payload.get("error")
    refs = []
    for it in payload.get("items") or []:
        kind = it.get("kind") or "crm"
        title = str(it.get("title") or "")[:40]
        if title:
            refs.append(f"crm:{kind}:{title}")
    claim = ClaimBinding(
        claim=f"CRM hits={payload.get('n_hits') or 0}",
        evidence_refs=refs[:8],
        status="grounded" if (payload.get("n_hits") or 0) else "unsupported",
        reason="notion_crm_search",
    )
    return ToolResult(
        ok=ok,
        tool_id="crm.search",
        error=str(payload.get("error") or ""),
        payload=payload,
        evidence_refs=refs[:12],
        claim_bindings=[claim],
    )
