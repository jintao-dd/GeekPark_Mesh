"""多路召回 + 融合 rerank + 向量检索。"""
from __future__ import annotations

import re
from typing import Any

from . import embeddings, item_facts, qa_structured, search
from .ask_scope import AskScope


def _hit_team_allowed(h: dict, team: str) -> bool:
    """团队视角：条目按 owner_team；FTS 章节需在正文/标题含团队名。"""
    if not team:
        return True
    ot = (h.get("owner_team") or "").strip()
    if ot:
        return ot == team
    blob = f"{h.get('title') or ''} {h.get('body') or ''}"
    return team in blob


def rerank_hits(hits: list[dict], q: str, limit: int = 24) -> list[dict]:
    """规则 rerank：专名命中、章节权重、来源层。"""
    terms = set(re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", q or ""))
    out: list[dict] = []
    for h in hits:
        h = dict(h)
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
        h["score"] = float(h.get("score") or 0) + bonus
        out.append(h)
    out.sort(key=lambda x: float(x.get("score") or 0))
    return out[:limit]


def _hybrid_recall(
    con,
    search_q: str,
    scope: AskScope,
    *,
    limit: int,
) -> tuple[list[dict], dict]:
    slug = scope.slug
    team = scope.team_filter
    date_from, date_to = search.lexical_date_range(search_q)
    all_hits: list[dict] = []
    used_vector = False
    for variant in embeddings.expand_query(search_q):
        fts = search.fts_search(con, variant, slug=slug, limit=limit, date_from=date_from, date_to=date_to)
        items = item_facts.search(
            con, variant, slug=slug, team=team, date_from=date_from, date_to=date_to,
            limit=limit // 2 + 8,
        )
        merged = search.merge_hits(fts, items, limit=limit)
        if team:
            merged = [h for h in merged if _hit_team_allowed(h, team)]
        all_hits.extend(merged)
    if embeddings.is_configured():
        qvec = embeddings.embed_one(search_q)
        if qvec:
            used_vector = True
            vec_hits = embeddings.vector_search(
                con, qvec, slug=slug, team=team, date_from=date_from, date_to=date_to, limit=limit // 2,
            )
            all_hits.extend(vec_hits)
    deduped = search.merge_hits(all_hits, limit=limit * 2)
    ranked = rerank_hits(deduped, search_q, limit=limit)
    return ranked, {"date_from": date_from, "date_to": date_to, "used_vector": used_vector}


def retrieve(
    con,
    q: str,
    scope: AskScope,
    *,
    limit: int = 40,
    intent: dict | None = None,
) -> tuple[str, list[dict], dict]:
    """
    返回 (mode, hits, meta)。
    mode: structured | hybrid
    q 应为已合并历史的检索问句。
    intent 可选，避免重复 parse_intent。
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
        # 结构化失败时回退混合检索
        hits, meta = _hybrid_recall(con, search_q, scope, limit=limit)
        meta["structured_fallback"] = structured
        return "hybrid", hits, meta

    hits, meta = _hybrid_recall(con, search_q, scope, limit=limit)
    return "hybrid", hits, meta


def hits_to_contexts(hits: list[dict], q: str, *, limit: int = 40) -> list[dict]:
    ctxs = search.ask_contexts_from_hits(hits, limit=limit)
    date_from, date_to = search.lexical_date_range(q)
    if date_from or date_to:
        ctxs.insert(0, {
            "issue": "",
            "section": "检索范围",
            "title": "时间过滤",
            "body": f"from={date_from or '…'} to={date_to or '…'}",
        })
    return ctxs
