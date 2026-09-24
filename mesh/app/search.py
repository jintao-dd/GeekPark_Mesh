"""全文检索：中文走 toks 列 FTS5 MATCH（二元组），避免 LIKE 全表扫。"""
from __future__ import annotations
import datetime
import html
import os
import re
from . import tokenize as tok
from . import qa_structured

_DEDUP_WS = re.compile(r"\s+")

_SECTION_WEIGHT = {
    "本期概览": 5,
    "接触过的人和公司": 3,
    "关注了什么": 3,
    "抽取条目": 2,
    "可同步的关系": 2,
    "沟通中提到的看法": 2,
    "日程与计划": 1,
    "记录": 1,
    "本期未汇入": 0,
}


def fts_search(
    con,
    q: str,
    slug: str = "",
    limit: int = 40,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict]:
    """
    统一检索入口。返回 [{issue_slug, section, title, sn, body, date_end, score}...]
    date_from/date_to: YYYY-MM-DD；缺日期行不纳入时间窗。
    """
    q = (q or "").strip()
    if not q:
        return []
    from .issue_period import normalize_issue_slug
    slug = normalize_issue_slug(slug) or slug
    match_q = tok.build_match_query(q)
    hits: list[dict] = []

    def _date_sql(params: list) -> str:
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

    if match_q:
        if getattr(con, "dialect", "sqlite") == "postgresql":
            from . import fts_pg
            try:
                for r in fts_pg.search_fts(con, q, slug=slug, date_from=date_from, date_to=date_to, limit=limit):
                    body = r.get("body") or ""
                    hits.append({
                        "issue_slug": normalize_issue_slug(r["issue_slug"]) or r["issue_slug"],
                        "section": r["section"],
                        "title": r["title"],
                        "sn": _highlight(body, q),
                        "body": body,
                        "date_end": r.get("date_end") or "",
                        "score": float(r.get("score") or 0),
                    })
            except Exception:
                hits = []
        else:
            params: list = [match_q]
            sql = """
                SELECT issue_slug, section, title, body, date_end,
                       bm25(search_fts) AS score
                FROM search_fts
                WHERE search_fts MATCH ?
            """
            if slug:
                sql += " AND issue_slug = ?"
                params.append(slug)
            sql += _date_sql(params)
            sql += " ORDER BY bm25(search_fts) LIMIT ?"
            params.append(max(limit * 3, limit))
            try:
                for r in con.execute(sql, params):
                    body = r["body"] or ""
                    hits.append({
                        "issue_slug": normalize_issue_slug(r["issue_slug"]) or r["issue_slug"],
                        "section": r["section"],
                        "title": r["title"],
                        "sn": _highlight(body, q),
                        "body": body,
                        "date_end": r["date_end"] or "",
                        "score": float(r["score"] or 0),
                    })
            except Exception:
                hits = []

    if len(hits) < min(3, limit):
        terms = tok.query_terms(q, limit=3)
        if terms:
            where = " OR ".join("(title LIKE ? OR body LIKE ?)" for _ in terms)
            params = []
            for t in terms:
                params += [f"%{t}%", f"%{t}%"]
            sql = f"SELECT issue_slug, section, title, body, date_end FROM search_fts WHERE ({where})"
            if slug:
                sql += " AND issue_slug = ?"
                params.append(slug)
            sql += _date_sql(params)
            sql += " LIMIT ?"
            params.append(limit)
            seen = {(h["issue_slug"], h["section"], h["title"]) for h in hits}
            try:
                for r in con.execute(sql, params):
                    norm_slug = normalize_issue_slug(r["issue_slug"]) or r["issue_slug"]
                    key = (norm_slug, r["section"], r["title"])
                    if key in seen:
                        continue
                    body = r["body"] or ""
                    hits.append({
                        "issue_slug": norm_slug,
                        "section": r["section"],
                        "title": r["title"],
                        "sn": _highlight(body, terms[0]),
                        "body": body,
                        "date_end": r["date_end"] or "",
                        "score": 5.0,
                    })
            except Exception:
                pass

    today = datetime.date.today()

    def _rank_key(h: dict):
        try:
            d = datetime.date.fromisoformat((h.get("date_end") or "")[:10])
            age_days = max(0, (today - d).days)
        except Exception:
            age_days = 3650
        blended = float(h.get("score") or 0) + age_days * 0.01
        return (
            0 if h["issue_slug"] == slug else 1,
            -_SECTION_WEIGHT.get(h.get("section") or "", 1),
            blended,
        )

    hits.sort(key=_rank_key)
    out = []
    for h in hits[:limit]:
        row = {
            "issue_slug": h["issue_slug"],
            "section": h["section"],
            "title": h["title"],
            "sn": h["sn"],
            "body": h.get("body") or "",
            "date_end": h.get("date_end") or "",
            "source": "fts",
        }
        if h.get("score") is not None:
            row["score"] = float(h["score"])
        out.append(row)
    return out


def _highlight(body: str, needle: str, width: int = 60) -> str:
    body = body or ""
    if not needle:
        return html.escape(body[:90])
    i = -1
    use = needle
    if needle.isascii():
        i = body.lower().find(needle.lower())
    else:
        i = body.find(needle)
    if i < 0:
        for seg in sorted(re.findall(r"[\u4e00-\u9fff]{2,}", needle), key=len, reverse=True):
            i = body.find(seg)
            if i >= 0:
                use = seg
                break
    if i < 0:
        return html.escape(body[:90])
    a = max(0, i - 30)
    frag = html.escape(body[a : i + len(use) + width])
    marked = html.escape(use)
    return frag.replace(marked, f"<mark>{marked}</mark>", 1)


def ask_contexts_from_hits(hits: list[dict], limit: int = 40) -> list[dict]:
    ctxs = []
    for h in hits[:limit]:
        ctx: dict = {
            "期号": h.get("issue_slug") or "",
            "章节": h.get("section") or "",
            "标题": h.get("title") or "",
            "内容": (h.get("body") or "")[:600],
        }
        if h.get("owner_team"):
            ctx["归属团队"] = h["owner_team"]
        if h.get("source") == "item_facts":
            ctx["来源层"] = "条目索引"
            if h.get("item_id"):
                ctx["条目ID"] = h["item_id"]
        elif h.get("source") == "vector":
                ctx["来源层"] = "向量"
        if h.get("chunk_id"):
            ctx["chunk_id"] = h["chunk_id"]
        if h.get("item_id") is not None and "条目ID" not in ctx:
            ctx["条目ID"] = h["item_id"]
        ctxs.append(ctx)
    return ctxs


def _body_fingerprint(body: str, width: int = 160) -> str:
    """跨 FTS/向量/条目去重：归一化正文前缀。"""
    b = _DEDUP_WS.sub("", (body or "")[:width]).lower()
    return b[:120]


def hit_dedup_key(h: dict) -> tuple:
    """重复检测：同一 item / chunk / 正文片段只保留得分最优的一条。"""
    fp = _body_fingerprint(h.get("body") or "")
    if fp and len(fp) >= 16:
        return ("body", h.get("issue_slug"), fp)
    if h.get("item_id"):
        return ("item", h.get("issue_slug"), int(h["item_id"]))
    cid = h.get("chunk_id")
    if cid:
        return ("chunk", cid)
    title = _DEDUP_WS.sub("", (h.get("title") or "")[:80]).lower()
    return ("text", h.get("issue_slug"), h.get("section"), title, fp)


# ---------------------------------------------------------------------------
# 融合层：加权 RRF（Reciprocal Rank Fusion）
#
# 背景：PG 上各通道原始分量纲不可比 —— 词法通道是「命中词计数」小整数(<=0)，
# 向量通道是 -cosine∈[-1,0]，LIKE 兜底是人为常量。在 merge_hits 里直接比大小
# 无意义：有强词法命中时向量候选被系统性压后（向量收益传不到排序层）。
#
# RRF 只用「通道内位次」，天然免疫量纲差异：
#     score(d) = Σ_g  w_g * section_weight(d) / (K + rank_g(d))
# 同一文档被多路召回时贡献累加（共识加分），score 越大越靠前。
# 通道权重可用 MESH_RRF_WEIGHTS="vector=1.2,item_facts=0.8" 调参。
# MESH_FUSION=legacy 可切回旧融合（仅用于 A/B 对照）。
# ---------------------------------------------------------------------------

RRF_K = 60.0


def rrf_unit() -> float:
    """一个 rank-1 通道贡献，作为后置加成的量纲单位（≈0.0164）。"""
    return 1.0 / (RRF_K + 1.0)


_RRF_DEFAULT_WEIGHTS = {
    "fts": 1.0,
    "item_facts": 1.0,
    "item_entity_facts": 0.5,  # 辅助通道：分数为硬编码常量、条数少，组内 rank1 会虚高
    "vector": 1.0,
}


def _rrf_weights() -> dict[str, float]:
    w = dict(_RRF_DEFAULT_WEIGHTS)
    raw = (os.environ.get("MESH_RRF_WEIGHTS") or "").strip()
    if not raw:
        return w
    for part in raw.split(","):
        if "=" not in part:
            continue
        k, _, v = part.partition("=")
        try:
            w[k.strip()] = float(v.strip())
        except ValueError:
            continue
    return w


def _collapse_channel(hits: list[dict]) -> list[dict]:
    """通道内折叠：按去重键保留最优（最小）原始分，并保持通道原生排序。

    多 query 变体会各自产出一份通道结果；直接拼接会让「近并列分数」被打散，
    丢掉通道自身的相关性序（RRF 只吃位次，序错了就全错）。这里先折叠再进 RRF。
    """
    best: dict[tuple, dict] = {}
    for h in hits or []:
        key = hit_dedup_key(h)
        sc = float(h.get("score") or 0.0)
        prev = best.get(key)
        if prev is None or sc < float(prev.get("score") or 0.0):
            best[key] = h
    return sorted(best.values(), key=lambda x: float(x.get("score") or 0.0))


def merge_hits_rrf(*groups: list[dict], limit: int = 48) -> list[dict]:
    """加权 RRF 融合：各通道内部独立排序取 rank，丢弃跨通道原始分比较。"""
    weights = _rrf_weights()
    best: dict[tuple, dict] = {}
    for gi, hits in enumerate(groups):
        if not hits:
            continue
        ordered = _collapse_channel(hits)
        for rank, h in enumerate(ordered, 1):
            key = hit_dedup_key(h)
            w = float(weights.get(str(h.get("source") or ""), 1.0))
            sec_w = _SECTION_WEIGHT.get(h.get("section") or "", 1)
            # 章节权重退化为轻微先验（乘性），不再参与跨通道比分
            contrib = w * (1.0 + 0.05 * sec_w) / (RRF_K + rank)
            prev = best.get(key)
            if prev is None:
                cand = dict(h)
                cand["_raw_score"] = float(h.get("score") or 0.0)
                cand["_rrf_score"] = contrib
                cand["_rrf_ranks"] = {f"g{gi}": rank}
                best[key] = cand
            else:
                prev["_rrf_score"] = float(prev.get("_rrf_score") or 0.0) + contrib
                prev["_rrf_ranks"][f"g{gi}"] = rank
                if not (prev.get("body") or "") and (h.get("body") or ""):
                    prev["body"] = h["body"]
    merged = sorted(best.values(), key=lambda x: -float(x.get("_rrf_score") or 0.0))
    for h in merged:
        h["score"] = float(h.get("_rrf_score") or 0.0)
    return merged[:limit]


def merge_hits_legacy(*groups: list[dict], limit: int = 48) -> list[dict]:
    """旧融合（跨通道直接比原始分）：仅保留用于 A/B 对照，新代码勿用。"""
    best: dict[tuple, dict] = {}
    for hits in groups:
        for h in hits or []:
            key = hit_dedup_key(h)
            w = _SECTION_WEIGHT.get(h.get("section") or "", 1)
            cand = dict(h)
            # 分数越小越好（BM25 为负）；高权重章节减分以提升排序
            cand["score"] = float(cand.get("score") or 0) - w * 0.15
            prev = best.get(key)
            if prev is None or float(cand["score"]) < float(prev.get("score") or 0):
                best[key] = cand
    merged = sorted(best.values(), key=lambda x: float(x.get("score") or 0))
    return merged[:limit]


def merge_hits(*groups: list[dict], limit: int = 48) -> list[dict]:
    """融合入口：默认 RRF；MESH_FUSION=legacy 切回旧实现（A/B 对照）。"""
    mode = (os.environ.get("MESH_FUSION") or "rrf").strip().lower()
    if mode == "legacy":
        return merge_hits_legacy(*groups, limit=limit)
    return merge_hits_rrf(*groups, limit=limit)


def hybrid_search(
    con,
    q: str,
    *,
    slug: str = "",
    team: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 40,
) -> list[dict]:
    """读者页 FTS + 条目 item_facts 双轨召回（已发布语料）。"""
    from . import item_facts

    fts = fts_search(con, q, slug=slug, limit=limit, date_from=date_from, date_to=date_to)
    items = item_facts.search(
        con, q, slug=slug, team=team, date_from=date_from, date_to=date_to, limit=limit // 2 + 8,
    )
    return merge_hits(fts, items, limit=limit)


def lexical_date_range(q: str) -> tuple[str | None, str | None]:
    """主题题时间窗 (date_from, date_to)。默认近 MESH_QA_LEXICAL_WINDOW_DAYS。
    裸「过去」不算显式时间（避免反缩到 30 天）。"""
    q = q or ""
    if re.search(
        r"全部历史|所有期|历史以来|不限时间|有史以来|不限日期|全部时期|所有历史|历年|"
        r"过去\s*\d|近\s*\d|最近|近\s*\d|本月|上月|今年|去年|近期|这段时间|本周|上周|这周",
        q,
    ):
        df, dt, _ = qa_structured.parse_window(q)
        return df, dt
    try:
        days = int(os.environ.get("MESH_QA_LEXICAL_WINDOW_DAYS", "90") or "90")
    except Exception:
        days = 90
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat(), None


def lexical_date_from(q: str) -> str | None:
    """兼容旧调用：仅返回 date_from。"""
    df, _ = lexical_date_range(q)
    return df
