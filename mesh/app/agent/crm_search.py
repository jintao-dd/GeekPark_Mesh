"""Notion CRM 检索（硅谷/思琪·Lilyann 对外沟通底盘）。

People 为枢纽；Interactions=事件；Takes=判断。
支持 mode=stats（结构化计数）与 mode=cross（与周报交叉）。
不进 Published Ask 主链；source_tier=crm_prior。
永不返回 email / wechat。
"""
from __future__ import annotations

import re
from typing import Any

from .models import ClaimBinding, ToolResult

# CRM 字段里的对外名 → 对内称呼
_OWNER_DISPLAY = {
    "lilyann": "思琪（Lilyann）",
    "lilyanna": "思琪（Lilyann）",
    "sean shen": "Sean Shen",
}

_SIQI_ALIASES = frozenset({"思琪", "赵思琪", "lilyann", "lilyanna", "siqi"})

# 触发 CRM 的关键词（与 supervisor.plan._CRM_ASK_RE 对齐）
_CRM_CTX_RE = re.compile(
    r"(硅谷|CRM|人脉库|对外人脉|创业者库|Notion\s*CRM|思琪侧|海外\s*BD|\bBD\b|湾区|思琪)",
    re.I,
)
_WEEKLY_EXPLICIT_RE = re.compile(r"(周报|已上线|本期|上期|往期|期次)", re.I)
_COUNT_RE = re.compile(
    r"(多少|几[个位家]|一共|总共|总数|规模|计数|有几)",
    re.I,
)
_ARCHIVE_RE = re.compile(
    r"(档案|人脉库|底库|一共有多少人|总共有多少人|CRM\s*里?有多少人|库里有多少)",
    re.I,
)
_COMPANY_RE = re.compile(r"(公司|企业|机构)", re.I)
_TAKE_RE = re.compile(r"(判断|Take|takes|跟进判断|下一步|verdict)", re.I)
_TOUCH_RE = re.compile(
    r"(接触|沟通过|跟进过|见面|见过|聊过|拜访|对接|互动|沟通)",
    re.I,
)
_CROSS_RE = re.compile(
    r"(交叉|两边|周报里有没有|CRM\s*有但|周报有但|都出现|只有CRM|只有周报|"
    r"CRM.*周报|周报.*CRM|人脉库.*周报|周报.*人脉)",
    re.I,
)

_METRIC_LABELS = {
    "people_archive": "人脉档案人数",
    "people_touched": "沟通过的人数（Interactions 去重）",
    "companies": "公司档案数",
    "takes": "有判断的 Takes 数",
}


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


def is_crm_context(q: str) -> bool:
    return bool(_CRM_CTX_RE.search(q or ""))


def is_weekly_explicit(q: str) -> bool:
    return bool(_WEEKLY_EXPLICIT_RE.search(q or ""))


def is_crm_count_question(q: str) -> bool:
    """硅谷/CRM 语境下的计数问句 → 应走 stats，不走周报 count。"""
    q = q or ""
    if not is_crm_context(q):
        return False
    if is_weekly_explicit(q):
        return False
    return bool(_COUNT_RE.search(q))


def is_crm_cross_question(q: str) -> bool:
    q = q or ""
    if not _CROSS_RE.search(q):
        return False
    return is_crm_context(q) or is_weekly_explicit(q) or bool(re.search(r"周报", q))


def infer_crm_metric(q: str) -> str:
    """从问句推断 stats metric。"""
    q = q or ""
    if _TAKE_RE.search(q) and not _TOUCH_RE.search(q):
        return "takes"
    if _COMPANY_RE.search(q) and not re.search(r"多少人|几个人", q):
        return "companies"
    if _ARCHIVE_RE.search(q) and not _TOUCH_RE.search(q):
        return "people_archive"
    if _TOUCH_RE.search(q) or re.search(r"接触了多少|跟进了多少|见了多少", q):
        return "people_touched"
    if re.search(r"多少人|几个人", q):
        if _ARCHIVE_RE.search(q) or re.search(r"一共|总共|库里|底库", q):
            return "people_archive"
        return "people_touched"
    if re.search(r"多少家|几家公司", q):
        return "companies"
    return "people_archive"


def _split_people_names(raw: str) -> list[str]:
    out: list[str] = []
    for part in re.split(r"[,，、/;；|]", raw or ""):
        name = part.strip()
        if not name or name in out:
            continue
        out.append(name)
    return out


def stats_crm(
    con,
    *,
    metric: str = "people_archive",
    date_from: str | None = None,
    date_to: str | None = None,
    sample_limit: int = 12,
    query: str = "",
) -> dict[str, Any]:
    """结构化计数。total 来自 SQL/去重，禁止用 sample 长度冒充。"""
    m = (metric or "people_archive").strip().lower()
    if m not in _METRIC_LABELS:
        m = "people_archive"
    sample: list[dict[str, Any]] = []
    total = 0
    event_count = 0
    caveats: list[str] = []
    window = {"date_from": date_from or "", "date_to": date_to or ""}

    if m == "people_archive":
        total = int(con.execute("SELECT COUNT(*) AS c FROM crm_people").fetchone()["c"] or 0)
        rows = con.execute(
            """
            SELECT display_name, company_names, headline, last_touched
            FROM crm_people
            ORDER BY
              CASE WHEN last_touched IS NOT NULL AND TRIM(last_touched) != '' THEN 0 ELSE 1 END,
              display_name
            LIMIT ?
            """,
            (int(sample_limit),),
        ).fetchall()
        sample = [
            {
                "name": r["display_name"] or "",
                "company": r["company_names"] or "",
                "headline": r["headline"] or "",
                "last_touched": r["last_touched"] or "",
            }
            for r in rows
        ]
        caveats.append("这是 Notion CRM 人脉档案总数，进库不等于沟通过。")
    elif m == "companies":
        total = int(con.execute("SELECT COUNT(*) AS c FROM crm_companies").fetchone()["c"] or 0)
        rows = con.execute(
            "SELECT name, one_liner, sector, stage FROM crm_companies ORDER BY name LIMIT ?",
            (int(sample_limit),),
        ).fetchall()
        sample = [
            {
                "name": r["name"] or "",
                "one_liner": r["one_liner"] or "",
                "sector": r["sector"] or "",
                "stage": r["stage"] or "",
            }
            for r in rows
        ]
        caveats.append("这是公司档案数，不是「接触过的公司」去重。")
    elif m == "takes":
        where = "verdict IS NOT NULL AND TRIM(verdict) != ''"
        params: list[Any] = []
        if date_from:
            where += " AND last_reviewed IS NOT NULL AND last_reviewed >= ?"
            params.append(date_from)
        if date_to:
            where += " AND last_reviewed IS NOT NULL AND last_reviewed <= ?"
            params.append(date_to)
        total = int(
            con.execute(f"SELECT COUNT(*) AS c FROM crm_takes WHERE {where}", params).fetchone()["c"]
            or 0
        )
        rows = con.execute(
            f"""
            SELECT name, person_names, verdict, scenario, owner, last_reviewed
            FROM crm_takes WHERE {where}
            ORDER BY last_reviewed DESC, name LIMIT ?
            """,
            [*params, int(sample_limit)],
        ).fetchall()
        sample = [
            {
                "person": r["person_names"] or "",
                "name": r["name"] or "",
                "verdict": (r["verdict"] or "")[:160],
                "owner": _owner_label(r["owner"] or "Lilyann"),
                "last_reviewed": r["last_reviewed"] or "",
            }
            for r in rows
        ]
        caveats.append("Takes 是跟进判断，属 CRM 语境，不是已上线周报事实。")
    else:  # people_touched
        where = "1=1"
        params: list[Any] = []
        if date_from:
            where += " AND date_start IS NOT NULL AND date_start >= ?"
            params.append(date_from)
        if date_to:
            where += " AND date_start IS NOT NULL AND date_start <= ?"
            params.append(date_to)
        rows = con.execute(
            f"""
            SELECT date_start, interact_type, people_names, title, our_side
            FROM crm_interactions WHERE {where}
            ORDER BY date_start DESC, title DESC
            """,
            params,
        ).fetchall()
        event_count = len(rows)
        seen: dict[str, dict[str, Any]] = {}
        for r in rows:
            names = _split_people_names(r["people_names"] or "")
            for name in names:
                key = name.casefold()
                if key not in seen:
                    seen[key] = {
                        "name": name,
                        "last_date": r["date_start"] or "",
                        "last_type": r["interact_type"] or "",
                        "events": 1,
                    }
                else:
                    seen[key]["events"] = int(seen[key].get("events") or 0) + 1
                    if (r["date_start"] or "") > (seen[key].get("last_date") or ""):
                        seen[key]["last_date"] = r["date_start"] or ""
                        seen[key]["last_type"] = r["interact_type"] or ""
        ordered = sorted(
            seen.values(),
            key=lambda x: (x.get("last_date") or "", x.get("name") or ""),
            reverse=True,
        )
        total = len(ordered)
        sample = ordered[: int(sample_limit)]
        ix_total = int(con.execute("SELECT COUNT(*) AS c FROM crm_interactions").fetchone()["c"] or 0)
        caveats.append(
            f"按沟通事件（Interactions）去重计人；含会前准备等类型。"
            f"当前底库共同步 {ix_total} 条沟通事件"
            + (f"，本窗命中 {event_count} 条" if (date_from or date_to) else "")
            + "——不是完整历史，勿当全年真实总量。"
        )
        if event_count and total:
            caveats.append(
                f"沟通事件 {event_count} 次 / 去重人数 {total}（同一人多次不计多人）。"
            )

    label = _METRIC_LABELS[m]
    lines = [
        "【硅谷 CRM · 结构化统计】",
        "说明：来自 Notion CRM 底库，不是已上线周报。",
        f"口径：{label}",
        f"总数：**{total}**",
    ]
    if query:
        lines.append(f"查询：{query}")
    if date_from or date_to:
        lines.append(f"时间窗：{date_from or '…'} → {date_to or '至今'}")
    for c in caveats:
        lines.append(f"注意：{c}")
    if sample:
        lines.append("")
        lines.append(f"## 样例（最多 {len(sample)} 条，不是总数）")
        for it in sample:
            if m == "people_touched":
                bit = f"- **{it.get('name')}**"
                if it.get("last_date"):
                    bit += f"（最近 {it['last_date']} · {it.get('last_type') or '沟通'}）"
                if it.get("events"):
                    bit += f" · {it['events']} 次"
                lines.append(bit)
            elif m == "companies":
                bit = f"- **{it.get('name')}**"
                if it.get("sector"):
                    bit += f"｜{it['sector']}"
                lines.append(bit)
            elif m == "takes":
                who = it.get("person") or it.get("name") or "（未挂人）"
                lines.append(f"- **{who}**：{(it.get('verdict') or '')[:80]}")
            else:
                bit = f"- **{it.get('name')}**"
                if it.get("company"):
                    bit += f" @ {it['company']}"
                lines.append(bit)

    return {
        "ok": True,
        "empty": total <= 0,
        "mode": "stats",
        "metric": m,
        "metric_label": label,
        "total": total,
        "event_count": event_count,
        "sample": sample,
        "sample_n": len(sample),
        "window": window,
        "caveats": caveats,
        "query": query,
        "text": "\n".join(lines),
        "source_tier": "crm_prior",
        "owner_line": "思琪（Lilyann）· 硅谷 CRM",
        "items": [
            {
                "title": it.get("name") or it.get("person") or label,
                "snippet": str(
                    it.get("verdict") or it.get("company") or it.get("last_date") or ""
                )[:80],
                "kind": f"stats:{m}",
            }
            for it in sample[:8]
        ],
        "n_hits": total,
        "crm_metric": m,
    }


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
            SELECT notion_id, name, person_names, verdict, scenario, owner, last_reviewed, is_prospect
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
            SELECT notion_id, name, person_names, verdict, scenario, owner, last_reviewed, is_prospect
            FROM crm_takes
            WHERE person_names LIKE ? OR name LIKE ? OR verdict LIKE ? OR scenario LIKE ?
            ORDER BY last_reviewed DESC, name
            LIMIT ?
            """,
            (_like(q), _like(q), _like(q), _like(q), int(limit)),
        ).fetchall()
    return [
        {
            "notion_id": r["notion_id"] or "",
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
    if m in ("person", "company", "recent", "take", "stats", "cross"):
        return m
    # 意图由 planner（LLM）决定；这里不再用正则猜 stats/cross，缺就走 auto
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
            # Take 页面正文 = 逐次沟通/判断变更的完整时间线，单条 verdict 装不下。
            tl = (t.get("timeline") or "").strip()
            if tl:
                lines.append("  <跟进记录>")
                for ln in tl.splitlines():
                    if ln.strip():
                        lines.append(f"  {ln}")
                lines.append("  </跟进记录>")

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
    metric: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    cross_op: str = "",
    entity_kind: str = "person",
    weekly_team: str = "",
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
            "n_hits": 0,
        }

    m = _infer_mode(q, mode)

    if m == "stats":
        from .. import qa_structured

        met = (metric or "").strip() or infer_crm_metric(q)
        df, dt = date_from, date_to
        if not df and not dt and met in ("people_touched", "takes"):
            w_from, w_to, _days = qa_structured.parse_window(q)
            df, dt = w_from, w_to
        return stats_crm(
            con,
            metric=met,
            date_from=df,
            date_to=dt,
            sample_limit=max(3, min(int(limit), 20)),
            query=q,
        )

    if m == "cross":
        from . import crm_cross

        op = (cross_op or "").strip() or crm_cross.infer_cross_op(q)
        kind = (entity_kind or "person").strip() or crm_cross.infer_entity_kind(q)
        df, dt = date_from, date_to
        if not df and not dt:
            from .. import qa_structured

            w_from, w_to, _days = qa_structured.parse_window(q)
            df, dt = w_from, w_to
        return crm_cross.cross_crm_weekly(
            con,
            op=op,
            entity_kind=kind,
            date_from=df,
            date_to=dt,
            weekly_team=weekly_team,
            query=q,
            limit=max(5, min(int(limit), 40)),
        )

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
        seen: set = set()
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
    else:
        people = _search_people(con, q, limit=limit)
        takes = _search_takes(con, q, limit=limit)
        interactions = _search_interactions(con, q, limit=min(8, limit))
        companies = _search_companies(con, q, limit=5)
        if not (people or takes or interactions or companies):
            interactions = _search_interactions(con, "", recent=True, limit=limit)

    # 命中 Take 时挂上页面正文（详细沟通记录）。只取前几条，避免上下文爆掉。
    if takes:
        from .. import notion_crm

        for t in takes[:3]:
            nid = t.get("notion_id") or ""
            if nid:
                t["timeline"] = notion_crm.page_timeline_text(con, nid)

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
        items.append(
            {
                "title": t.get("person") or t.get("name"),
                "snippet": t.get("verdict"),
                "kind": "take",
            }
        )
        if (t.get("timeline") or "").strip():
            items.append(
                {
                    "title": f"{t.get('person') or t.get('name')} · 跟进记录",
                    "snippet": t["timeline"],
                    "kind": "take_timeline",
                }
            )
    for ix in interactions:
        items.append(
            {
                "title": ix.get("title") or ix.get("people"),
                "snippet": f"{ix.get('date')} {ix.get('type')}",
                "kind": "interaction",
            }
        )
    for p in people:
        items.append(
            {
                "title": p.get("name"),
                "snippet": p.get("company") or p.get("headline"),
                "kind": "person",
            }
        )

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
    metric = str(args.get("metric") or "").strip()
    date_from = str(args.get("date_from") or "").strip() or None
    date_to = str(args.get("date_to") or "").strip() or None
    cross_op = str(args.get("cross_op") or args.get("op") or "").strip()
    entity_kind = str(args.get("entity_kind") or args.get("kind") or "person").strip()
    weekly_team = str(args.get("weekly_team") or args.get("team") or "").strip()
    payload = search_crm(
        con,
        query=q,
        mode=mode,
        limit=limit,
        metric=metric,
        date_from=date_from,
        date_to=date_to,
        cross_op=cross_op,
        entity_kind=entity_kind,
        weekly_team=weekly_team,
    )
    ok = bool(payload.get("ok")) and not payload.get("error")
    refs = []
    if payload.get("mode") == "stats":
        refs.append(f"crm:stats:{payload.get('metric')}:{payload.get('total')}")
    elif payload.get("mode") == "cross":
        refs.append(f"crm:cross:{payload.get('cross_op')}:{payload.get('n_hits')}")
    for it in payload.get("items") or []:
        kind = it.get("kind") or "crm"
        title = str(it.get("title") or "")[:40]
        if title:
            refs.append(f"crm:{kind}:{title}")
    n = int(payload.get("n_hits") or 0)
    claim = ClaimBinding(
        claim=(
            f"CRM {payload.get('metric_label') or payload.get('mode')}={payload.get('total', n)}"
            if payload.get("mode") == "stats"
            else f"CRM hits={n}"
        ),
        evidence_refs=refs[:8],
        status="grounded" if n or payload.get("mode") == "stats" else "unsupported",
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
