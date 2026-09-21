"""条目级事实索引：发刊后从 publish 快照物化，供搜索/问答结构化召回。"""
from __future__ import annotations

import json
import re
from typing import Any

from . import db, ingest, tokenize as tok
from .aggregator import INVALID_OWNER_TEAMS

SECTION = "抽取条目"
_SNIP = 480
_NAME_RE = re.compile(r"[\u4e00-\u9fff]{2,24}|[A-Za-z][A-Za-z0-9 .&\-]{2,40}")

# 召回兜底分（P1 止血）：只做「比噪声 MATCH 略好」的弱提示，不再用 -50 钉榜首。
# 约定：分数越小越靠前。噪声 MATCH = -1.0（命中 1 词）→ 兜底取 -1.5。
_FALLBACK_SCORE = -1.5
# 完全无词法命中时的兜底（原 -0.5，同样错误地排在 -1.0 之前）→ 退到 -1.2。
_FALLBACK_SCORE_NO_HIT = -1.2


def _parse_json_list(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        v = json.loads(raw)
        return [str(x).strip() for x in v if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        return []


def primary_name(entities: list[str], text: str) -> str:
    for n in entities:
        if n and len(n.strip()) >= 2:
            return n.strip()[:120]
    m = _NAME_RE.search(text or "")
    if m:
        return m.group(0)[:120]
    t = (text or "").strip()
    return t[:80] if t else "未命名"


def infer_entity_kind(name: str, roles: list[str], stype: str) -> str:
    roles_l = " ".join(roles).lower()
    if any(k in roles_l for k in ("客户", "意向")):
        return "company"
    if stype in ("T3", "T6", "T10") and any(
        k in (name or "") for k in ("公司", "科技", "智能", "Lab", "AI")
    ):
        return "company"
    if stype in ("T2", "T5", "T7"):
        return "topic"
    if len(name or "") <= 4 and re.search(r"[\u4e00-\u9fff]", name or ""):
        return "person"
    return "unknown"


def row_from_item(
    item: dict,
    *,
    issue_slug: str,
    issue_id: int,
    date_start: str,
    date_end: str,
) -> dict[str, Any]:
    entities = _parse_json_list(item.get("entities"))
    roles = _parse_json_list(item.get("roles"))
    signals = _parse_json_list(item.get("signals"))
    text = (item.get("text") or "").strip()
    name = primary_name(entities, text)
    owner = (item.get("owner_team") or item.get("team") or "").strip()
    stype = (item.get("stype") or "").strip()
    snippet = text[:_SNIP]
    title = " · ".join(x for x in (name, owner, stype) if x)[:200]
    body = " ".join(
        x
        for x in (
            text,
            owner,
            stype,
            " ".join(entities),
            " ".join(roles),
            item.get("source_label") or "",
        )
        if x
    )
    meta = {
        "entity_kind": infer_entity_kind(name, roles, stype),
        "entity_count": len(entities),
        "channel": item.get("channel") or "manual",
    }
    return {
        "issue_slug": issue_slug,
        "issue_id": issue_id,
        "date_start": date_start or "",
        "date_end": date_end or "",
        "item_id": int(item.get("id") or 0),
        "source_id": int(item.get("source_id") or 0),
        "owner_team": owner,
        "stype": stype,
        "zone": int(item.get("zone") or 4),
        "level": (item.get("level") or "L1").upper(),
        "kind": item.get("kind") or "fact",
        "primary_name": name,
        "entities_json": json.dumps(entities, ensure_ascii=False),
        "roles_json": json.dumps(roles, ensure_ascii=False),
        "signals_json": json.dumps(signals, ensure_ascii=False),
        "text_snippet": snippet,
        "source_label": (item.get("source_label") or "")[:200],
        "channel": item.get("channel") or "manual",
        "toks": tok.tokenize_for_index(f"{title} {body}"),
        "meta_json": json.dumps(meta, ensure_ascii=False),
    }


def reindex_item_facts(con, issue_id: int) -> int:
    """仅从已发布期写入 item_facts；草稿/下线清空。返回写入条数。"""
    row = con.execute(
        "SELECT id, slug, status, date_start, date_end FROM issues WHERE id=?",
        (issue_id,),
    ).fetchone()
    if not row:
        return 0
    slug = row["slug"]
    con.execute("DELETE FROM item_facts WHERE issue_slug=?", (slug,))
    con.execute("DELETE FROM item_entity_facts WHERE issue_slug=?", (slug,))
    if row["status"] != "published":
        sync_fts(con)
        return 0
    invalid = tuple(INVALID_OWNER_TEAMS)
    n = 0
    for it in db.iter_index_items(con, issue_id):
        ot = (it.get("owner_team") or "").strip()
        if not ot or ot in invalid:
            continue
        rec = row_from_item(
            it,
            issue_slug=slug,
            issue_id=row["id"],
            date_start=row["date_start"] or "",
            date_end=row["date_end"] or "",
        )
        if not rec["owner_team"]:
            continue
        con.execute(
            """INSERT INTO item_facts(
               issue_slug, issue_id, date_start, date_end, item_id, source_id,
               owner_team, stype, zone, level, kind,
               primary_name, entities_json, roles_json, signals_json,
               text_snippet, source_label, channel, toks, meta_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["issue_slug"], rec["issue_id"], rec["date_start"], rec["date_end"],
                rec["item_id"], rec["source_id"], rec["owner_team"], rec["stype"],
                rec["zone"], rec["level"], rec["kind"], rec["primary_name"],
                rec["entities_json"], rec["roles_json"], rec["signals_json"],
                rec["text_snippet"], rec["source_label"], rec["channel"],
                rec["toks"], rec["meta_json"],
            ),
        )
        n += 1
        _insert_entity_rows(con, rec, _parse_json_list(it.get("entities")))
    sync_fts(con)
    return n


def _insert_entity_rows(con, rec: dict, entities: list[str]) -> None:
    """一条 item 的每个实体各一行，便于按公司/人名精确召回。"""
    if not entities:
        return
    seen: set[str] = set()
    roles = _parse_json_list(rec.get("roles_json"))
    stype = rec.get("stype") or ""
    for raw in entities:
        name = (raw or "").strip()[:120]
        if len(name) < 2 or name in seen:
            continue
        seen.add(name)
        con.execute(
            """INSERT INTO item_entity_facts(
               issue_slug, issue_id, date_start, date_end, item_id, source_id,
               owner_team, stype, entity_name, entity_kind, text_snippet, source_label, channel)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec["issue_slug"], rec["issue_id"], rec["date_start"], rec["date_end"],
                rec["item_id"], rec["source_id"], rec["owner_team"], stype, name,
                infer_entity_kind(name, roles, stype),
                rec.get("text_snippet") or "", rec.get("source_label") or "",
                rec.get("channel") or "manual",
            ),
        )


def reindex_all_item_facts(con) -> int:
    n = 0
    for r in con.execute("SELECT id FROM issues WHERE status='published'"):
        n += reindex_item_facts(con, r["id"])
    return n


def sync_fts(con) -> None:
    """全量重建 item_facts_fts（条目量远小于原文，全量重建可接受）。"""
    con.execute("DELETE FROM item_facts_fts")
    for r in con.execute(
        """SELECT issue_slug, date_end, item_id, source_id, owner_team, stype,
                  primary_name, text_snippet, source_label, level, kind, zone, toks
           FROM item_facts"""
    ):
        con.execute(
            """INSERT INTO item_facts_fts(
               issue_slug, date_end, item_id, source_id, owner_team, stype,
               primary_name, text_snippet, source_label, level, kind, zone, toks)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                r["issue_slug"], r["date_end"] or "", r["item_id"], r["source_id"],
                r["owner_team"], r["stype"], r["primary_name"], r["text_snippet"],
                r["source_label"], r["level"], r["kind"], r["zone"], r["toks"],
            ),
        )


def _date_clause(date_from: str | None, date_to: str | None, params: list) -> str:
    sql = ""
    if date_from or date_to:
        sql += " AND date_end IS NOT NULL AND date_end != ''"
        if date_from:
            sql += " AND date_end >= ?"
            params.append(date_from)
        if date_to:
            sql += " AND date_end <= ?"
            params.append(date_to)
    return sql


def search_entities(
    con,
    q: str,
    *,
    slug: str = "",
    team: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 16,
) -> list[dict]:
    """按实体名 LIKE 召回（补 MATCH 漏掉的专名）。"""
    terms = tok.query_terms(q, limit=3)
    if not terms:
        return []
    hits: list[dict] = []
    for term in terms:
        if len(term) < 2:
            continue
        params: list[Any] = [f"%{term}%"]
        sql = """
            SELECT issue_slug, date_end, item_id, source_id, owner_team, stype,
                   entity_name, text_snippet, source_label
            FROM item_entity_facts WHERE entity_name LIKE ?
        """
        if slug:
            sql += " AND issue_slug = ?"
            params.append(slug)
        if team:
            sql += " AND owner_team = ?"
            params.append(ingest.canonical_team(team) or team)
        sql += _date_clause(date_from, date_to, params)
        sql += " LIMIT ?"
        params.append(limit)
        for r in con.execute(sql, params):
            d = dict(r)
            hits.append({
                "issue_slug": d["issue_slug"],
                "section": SECTION,
                "title": d.get("entity_name") or "",
                "body": " · ".join(
                    x for x in (
                        d.get("entity_name"), d.get("owner_team"), d.get("stype"),
                        d.get("text_snippet"), d.get("source_label"),
                    ) if x
                ),
                "date_end": d.get("date_end") or "",
                "score": -0.3,
                "item_id": d.get("item_id"),
                "owner_team": d.get("owner_team") or "",
                "stype": d.get("stype") or "",
                "source": "item_entity_facts",
            })
    return hits


def search(
    con,
    q: str,
    *,
    slug: str = "",
    team: str = "",
    stype: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 24,
) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    match_q = tok.build_match_query(q)
    params: list[Any] = []
    hits: list[dict] = []

    if match_q:
        if getattr(con, "dialect", "sqlite") == "postgresql":
            from . import fts_pg
            filt, fp, score_expr = fts_pg.build_toks_filter(
                match_q, ["toks", "primary_name", "text_snippet"]
            )
            if filt:
                sql = f"""
                    SELECT issue_slug, date_end, item_id, source_id, owner_team, stype,
                           primary_name, text_snippet, source_label, level, kind, zone,
                           -({score_expr}) AS score
                    FROM item_facts_fts WHERE {filt}
                """
                params = list(fp)
                if slug:
                    sql += " AND issue_slug = %s"
                    params.append(slug)
                if team:
                    sql += " AND owner_team = %s"
                    params.append(ingest.canonical_team(team) or team)
                if stype:
                    sql += " AND stype = %s"
                    params.append(stype)
                sql += _date_clause(date_from, date_to, params)
                sql += f" ORDER BY score DESC LIMIT %s"
                params.append(max(limit * 2, limit))
                try:
                    for r in con.execute(sql, params):
                        hits.append(_row_to_hit(dict(r), q))
                except Exception:
                    hits = []
        else:
            params.append(match_q)
            sql = """
                SELECT issue_slug, date_end, item_id, source_id, owner_team, stype,
                       primary_name, text_snippet, source_label, level, kind, zone,
                       bm25(item_facts_fts) AS score
                FROM item_facts_fts WHERE item_facts_fts MATCH ?
            """
            if slug:
                sql += " AND issue_slug = ?"
                params.append(slug)
            if team:
                sql += " AND owner_team = ?"
                params.append(ingest.canonical_team(team) or team)
            if stype:
                sql += " AND stype = ?"
                params.append(stype)
            sql += _date_clause(date_from, date_to, params)
            sql += " ORDER BY bm25(item_facts_fts) LIMIT ?"
            params.append(max(limit * 2, limit))
            try:
                for r in con.execute(sql, params):
                    hits.append(_row_to_hit(dict(r), q))
            except Exception:
                hits = []

    if len(hits) < min(3, limit):
        terms = tok.query_terms(q, limit=4)
        if terms:
            wh_params: list[Any] = []
            wh = []
            for t in terms:
                wh.append("(primary_name LIKE ? OR text_snippet LIKE ? OR toks LIKE ?)")
                pat = f"%{t}%"
                wh_params.extend([pat, pat, pat])
            sql2 = f"SELECT issue_slug, date_end, item_id, source_id, owner_team, stype, primary_name, text_snippet, source_label, level, kind, zone FROM item_facts WHERE ({' OR '.join(wh)})"
            if slug:
                sql2 += " AND issue_slug = ?"
                wh_params.append(slug)
            if team:
                sql2 += " AND owner_team = ?"
                wh_params.append(ingest.canonical_team(team) or team)
            if stype:
                sql2 += " AND stype = ?"
                wh_params.append(stype)
            sql2 += _date_clause(date_from, date_to, wh_params)
            sql2 += " LIMIT ?"
            wh_params.append(limit)
            seen = {(h["issue_slug"], h.get("item_id")) for h in hits}
            for r in con.execute(sql2, wh_params):
                d = dict(r)
                key = (d["issue_slug"], d.get("item_id"))
                if key in seen:
                    continue
                hits.append(_row_to_hit(d, q, score=_FALLBACK_SCORE_NO_HIT))

    # MATCH 已饱和时仍用 query_terms 补召回（并列主题/专名常被宽 OR 噪声挤出）
    terms_extra = tok.query_terms(q, limit=4)
    if terms_extra and len(hits) >= min(3, limit):
        wh_params = []
        wh = []
        for t in terms_extra:
            if len(t) < 2:
                continue
            wh.append("(primary_name LIKE ? OR text_snippet LIKE ? OR toks LIKE ?)")
            pat = f"%{t}%"
            wh_params.extend([pat, pat, pat])
        if wh:
            sql2 = (
                "SELECT issue_slug, date_end, item_id, source_id, owner_team, stype, "
                "primary_name, text_snippet, source_label, level, kind, zone "
                f"FROM item_facts WHERE ({' OR '.join(wh)})"
            )
            if slug:
                sql2 += " AND issue_slug = ?"
                wh_params.append(slug)
            if team:
                sql2 += " AND owner_team = ?"
                wh_params.append(ingest.canonical_team(team) or team)
            if stype:
                sql2 += " AND stype = ?"
                wh_params.append(stype)
            sql2 += _date_clause(date_from, date_to, wh_params)
            sql2 += " LIMIT ?"
            wh_params.append(max(limit, 24))
            seen = {(h["issue_slug"], h.get("item_id")) for h in hits}
            extras: list[dict] = []
            for r in con.execute(sql2, wh_params):
                d = dict(r)
                key = (d["issue_slug"], d.get("item_id"))
                if key in seen:
                    continue
                # P1 止血：原为 -50.0，在「越小越好」约定下反而钉榜首（符号写反）。
                # 改为略优于噪声 MATCH 的弱提示，真实词法命中不再被挤出候选池。
                extras.append(_row_to_hit(d, q, score=_FALLBACK_SCORE))
                seen.add(key)
            # 短词命中优先（端侧/座舱），避免被「模型/智能」宽匹配占满 LIMIT
            def _extra_key(h: dict) -> tuple:
                body = (h.get("body") or "") + (h.get("title") or "")
                short_hit = any(
                    len(t) <= 2 and t in body for t in terms_extra
                )
                return (0 if short_hit else 1, str(h.get("item_id") or ""))

            extras.sort(key=_extra_key)
            hits = extras + hits

    ent_hits = search_entities(con, q, slug=slug, team=team, date_from=date_from, date_to=date_to, limit=limit)
    seen = {(h["issue_slug"], h.get("item_id"), h.get("title")) for h in hits}
    for h in ent_hits:
        key = (h["issue_slug"], h.get("item_id"), h.get("title"))
        if key not in seen:
            hits.append(h)
            seen.add(key)

    hits.sort(key=lambda x: float(x.get("score") or 0))
    return hits[:limit]


def _row_to_hit(r: dict, q: str, score: float | None = None) -> dict:
    body = " · ".join(
        x
        for x in (
            r.get("primary_name") or "",
            r.get("owner_team") or "",
            r.get("stype") or "",
            r.get("text_snippet") or "",
            r.get("source_label") or "",
        )
        if x
    )
    sc = float(r["score"]) if score is None and r.get("score") is not None else (score if score is not None else 0.0)
    return {
        "issue_slug": r["issue_slug"],
        "section": SECTION,
        "title": r.get("primary_name") or "",
        "body": body,
        "date_end": r.get("date_end") or "",
        "score": sc,
        "item_id": r.get("item_id"),
        "source_id": r.get("source_id"),
        "owner_team": r.get("owner_team") or "",
        "stype": r.get("stype") or "",
        "kind": r.get("kind") or "",
        "source": "item_facts",
    }


def contexts_from_hits(hits: list[dict], limit: int = 24) -> list[dict]:
    out = []
    for h in hits[:limit]:
        ctx: dict[str, Any] = {
            "期号": h["issue_slug"],
            "章节": SECTION,
            "标题": h.get("title") or "",
            "内容": (h.get("body") or "")[:600],
            "来源层": "条目索引",
        }
        if h.get("item_id"):
            ctx["条目ID"] = h["item_id"]
        if h.get("owner_team"):
            ctx["归属团队"] = h["owner_team"]
        if h.get("stype"):
            ctx["类型"] = h["stype"]
        out.append(ctx)
    return out
