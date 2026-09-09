"""多路召回 + 融合 rerank + 向量检索。"""

from __future__ import annotations



import datetime

import re

from typing import Any



from . import embeddings, item_facts, qa_structured, search

from .ranking_quality import apply_ranking_v1_4

from .ask_scope import AskScope

from .owner_guard import parse_section_team

from . import ingest





def _hit_team_allowed(h: dict, team: str) -> bool:

    """团队视角：结构化 owner_team 匹配；周报 digest 节无归属时不误杀。"""

    if not team:

        return True

    ot = (h.get("owner_team") or "").strip()

    if ot:

        return ot == team

    src = h.get("source") or ""

    # 已发布 digest 节（无条目级归属）允许进入，避免团队视角零召回

    if src in ("fts", "") and not h.get("item_id"):

        sec = (h.get("section") or "").strip()

        if sec in ("接触过的人和公司", "关注了什么", "沟通中提到的看法", "可同步的关系", "日程与计划"):

            return True

    return False





def _enrich_owner_team(con, h: dict) -> dict:

    """为 FTS/章节命中补 owner_team（Data Source 标题、chunk_index、段标题）。"""

    h = dict(h)

    if (h.get("owner_team") or "").strip():

        return h

    sec = (h.get("section") or "").strip()

    title = (h.get("title") or "").strip()

    if sec == "Data Source" and title:

        canon = ingest.canonical_team(title) or qa_structured._normalize_team(title)

        if canon:

            h["owner_team"] = canon

            return h

    from_title = parse_section_team(title)

    if from_title:

        h["owner_team"] = from_title

        return h

    slug = h.get("issue_slug") or ""

    if slug and (sec or title):

        try:

            row = con.execute(

                """SELECT owner_team FROM chunk_index

                   WHERE issue_slug=? AND section=? AND title=? AND owner_team IS NOT NULL

                     AND owner_team != '' LIMIT 1""",

                (slug, sec, title),

            ).fetchone()

            if row and row["owner_team"]:

                h["owner_team"] = row["owner_team"]

        except Exception:

            pass

    return h





def _enrich_hits(con, hits: list[dict]) -> list[dict]:

    return [_enrich_owner_team(con, h) for h in hits]





def _term_overlap(q: str, h: dict) -> float:

    terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", q or ""))

    if not terms:

        return 0.0

    blob = f"{h.get('title') or ''} {h.get('body') or ''}".lower()

    hits = sum(1 for t in terms if t.lower() in blob)

    return hits / len(terms)





def rerank_hits(

    hits: list[dict],

    q: str,

    limit: int = 24,

    *,

    seed_chunk_ids: list[str] | None = None,

    seed_item_ids: list[str] | None = None,

    scope_meta: dict | None = None,

    apply_quality: bool | None = None,

) -> list[dict]:

    """规则 rerank：Quality Ranking v1.4（冻结）+ seed soft boost。

    apply_quality=None 时读环境 MESH_RANKING_QUALITY（默认 1）。
    评测 A/B 可设 MESH_RANKING_QUALITY=0 后自行套实验函数。
    """

    import os

    if apply_quality is None:
        apply_quality = os.environ.get("MESH_RANKING_QUALITY", "1").strip() != "0"

    seeds = {str(x) for x in (seed_chunk_ids or []) if x}

    seed_items = {str(x) for x in (seed_item_ids or []) if x}

    if apply_quality:

        out = apply_ranking_v1_4(list(hits), q, scope_meta=scope_meta)

    else:

        # legacy path（仅评测关闭 quality 时）：专名/来源/时效

        terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", q or ""))

        today = datetime.date.today()

        out = []

        for h0 in hits:

            h = dict(h0)

            bonus = 0.0

            title = (h.get("title") or "").lower()

            body = (h.get("body") or "").lower()

            for t in terms:

                tl = t.lower()

                if tl in title:

                    bonus += 0.25

                elif tl in body:

                    bonus += 0.08

            src = h.get("source") or ""

            if src == "item_entity_facts":

                bonus += 0.35

            elif src == "vector":

                bonus += 0.12

            elif src == "item_facts":

                bonus += 0.18

            try:

                d = datetime.date.fromisoformat((h.get("date_end") or "")[:10])

                age = max(0, (today - d).days)

                bonus -= min(age, 365) * 0.002

            except Exception:

                pass

            h["score"] = float(h.get("score") or 0) - bonus

            out.append(h)

        out.sort(key=lambda x: float(x.get("score") or 0))

    # seed soft boost（quality / legacy 共用；不改召回集合）

    if seeds or seed_items:

        boosted = []

        for h0 in out:

            h = dict(h0)

            bonus = 0.0

            overlap = _term_overlap(q, h)

            if seeds and str(h.get("chunk_id") or "") in seeds and overlap >= 0.12:

                bonus += 0.15

            iid = str(h.get("item_id") or "")

            if seed_items and iid and iid in seed_items and overlap >= 0.08:

                bonus += 0.2

            if bonus:

                h["score"] = float(h.get("score") or 0) - bonus

            boosted.append(h)

        boosted.sort(key=lambda x: float(x.get("score") or 0))

        out = boosted

    return out[:limit]


def _chunk_hint_terms(con, chunk_ids: list[str], limit: int = 12) -> list[str]:

    """从 seed chunk 取标题/实体名，扩展检索问句（不是直接当证据）。"""

    ids = [str(x) for x in chunk_ids if x][:limit]

    if not ids:

        return []

    placeholders = ",".join("?" * len(ids))

    try:

        rows = con.execute(

            f"SELECT title, entity_name, owner_team FROM chunk_index WHERE chunk_id IN ({placeholders})",

            ids,

        ).fetchall()

    except Exception:

        return []

    terms: list[str] = []

    for r in rows:

        for k in ("entity_name", "title", "owner_team"):

            v = str(r[k] or "").strip()

            if v and len(v) <= 40 and v not in terms:

                terms.append(v)

    return terms[:8]





def _resolve_date_window(scope: AskScope, search_q: str) -> tuple[str | None, str | None]:

    if scope.date_from or scope.date_to:

        return scope.date_from, scope.date_to

    return search.lexical_date_range(search_q)





def _hybrid_recall(

    con,

    search_q: str,

    scope: AskScope,

    *,

    limit: int,

    query_vec: list[float] | None = None,

    seed_chunk_ids: list[str] | None = None,

    seed_item_ids: list[str] | None = None,

) -> tuple[list[dict], dict]:

    slug = scope.slug

    team = scope.team_filter

    date_from, date_to = _resolve_date_window(scope, search_q)

    all_hits: list[dict] = []

    used_vector = False

    variants = embeddings.expand_query(search_q)

    min_hits_to_skip_expand = max(8, limit // 3)

    for i, variant in enumerate(variants):

        fts = search.fts_search(con, variant, slug=slug, limit=limit, date_from=date_from, date_to=date_to)

        for h in fts:

            h.setdefault("source", "fts")

        items = item_facts.search(

            con, variant, slug=slug, team=team, date_from=date_from, date_to=date_to,

            limit=limit // 2 + 8,

        )

        merged = search.merge_hits(fts, items, limit=limit)

        merged = _enrich_hits(con, merged)

        if team:

            merged = [h for h in merged if _hit_team_allowed(h, team)]

        all_hits.extend(merged)

        if i == 0 and len(merged) >= min_hits_to_skip_expand:

            break

    if embeddings.is_configured():

        qvec = query_vec if query_vec is not None else embeddings.embed_one(search_q)

        if qvec:

            used_vector = True

            vec_hits = embeddings.vector_search(

                con, qvec, slug=slug, team=team, date_from=date_from, date_to=date_to, limit=limit // 2,

            )

            all_hits.extend(vec_hits)

    deduped = search.merge_hits(all_hits, limit=limit * 2)

    deduped = _enrich_hits(con, deduped)

    ranked = rerank_hits(

        deduped, search_q, limit=limit,

        seed_chunk_ids=seed_chunk_ids, seed_item_ids=seed_item_ids,

        scope_meta={

            "context_slug": slug or "",

            "slug": slug or "",

            "date_from": date_from or "",

            "date_to": date_to or "",

        },

    )

    return ranked, {"date_from": date_from, "date_to": date_to, "used_vector": used_vector}





def retrieve(

    con,

    q: str,

    scope: AskScope,

    *,

    limit: int = 40,

    intent: dict | None = None,

    query_vec: list[float] | None = None,

    seed_chunk_ids: list[str] | None = None,

    seed_item_ids: list[str] | None = None,

) -> tuple[str, list[dict], dict]:

    """

    返回 (mode, hits, meta)。

    mode: structured | hybrid

    seed_chunk_ids / seed_item_ids: 上一轮定位种子，仅 soft boost。

    """

    search_q = (q or "").strip()

    if intent is None:

        intent = qa_structured.parse_intent(search_q)

    if intent and intent.get("type") != "error":

        structured = qa_structured.run_structured(

            con, intent, team_scope=scope.team_filter,

        )

        if structured.get("ok"):

            return "structured", [], {"structured": structured}

        hits, meta = _hybrid_recall(

            con, search_q, scope, limit=limit, query_vec=query_vec,

            seed_chunk_ids=seed_chunk_ids, seed_item_ids=seed_item_ids,

        )

        meta["structured_fallback"] = structured

        return "hybrid", hits, meta



    hits, meta = _hybrid_recall(

        con, search_q, scope, limit=limit, query_vec=query_vec,

        seed_chunk_ids=seed_chunk_ids, seed_item_ids=seed_item_ids,

    )

    return "hybrid", hits, meta





def hits_to_contexts(hits: list[dict], q: str, *, limit: int = 40) -> list[dict]:

    ctxs = search.ask_contexts_from_hits(hits, limit=limit)

    date_from, date_to = search.lexical_date_range(q)

    if date_from or date_to:

        ctxs.insert(0, {

            "期号": "",

            "章节": "检索范围",

            "标题": "时间过滤",

            "内容": f"from={date_from or '…'} to={date_to or '…'}",

        })

    return ctxs

