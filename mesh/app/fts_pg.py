"""PostgreSQL 全文检索：toks 列 + pg_trgm / LIKE，替代 SQLite FTS5 MATCH。"""
from __future__ import annotations

import re

from . import tokenize as tok


def _match_groups(match_q: str) -> list[list[str]]:
    """解析 FTS5 MATCH 表达式为 [[term,...], ...]（组间 AND，组内 OR）。"""
    groups: list[list[str]] = []
    for part in re.split(r"\s+AND\s+", (match_q or "").strip()):
        part = part.strip()
        if not part:
            continue
        if part.startswith("(") and part.endswith(")"):
            inner = part[1:-1]
            terms = [t.strip().strip('"') for t in re.split(r"\s+OR\s+", inner) if t.strip()]
        else:
            terms = [part.strip().strip('"')]
        terms = [t for t in terms if t]
        if terms:
            groups.append(terms)
    return groups


def _toks_clause(
    terms: list[str],
    columns: list[str],
    filter_params: list,
    score_parts: list[str],
    score_params: list,
) -> str:
    """一组 term OR → SQL 子句。

    filter_params / score_params 分开收集：score_expr 出现在 SELECT 列表，
    在 SQL 文本中先于 WHERE 的 filter，必须能按文本顺序独立拼接。
    """
    ors: list[str] = []
    for term in terms:
        pat = f"%{term}%"
        col_ors = []
        for col in columns:
            col_ors.append(f"{col} LIKE %s")
            filter_params.append(pat)
        ors.append(f"({' OR '.join(col_ors)})")
        score_parts.append("CASE WHEN toks LIKE %s THEN 1 ELSE 0 END")
        score_params.append(pat)
    return f"({' OR '.join(ors)})"


def build_toks_filter(match_q: str, columns: list[str]) -> tuple[str, list, list, str]:
    """
    返回 (WHERE 片段, filter_params, score_params, score_expr)。

    注意：score_expr 用在 SELECT 列表，filter 用在 WHERE；SQL 文本里 SELECT
    在前，所以拼接顺序必须是 ``score_params + filter_params``。
    两者分开返回，避免调用方按错误顺序拼参数（历史 bug：曾返回单列表
    ``filter_params + score_params``，导致占位符整体错位、过滤条件丢失）。
    """
    groups = _match_groups(match_q)
    if not groups:
        return "", [], [], "0"
    filter_params: list = []
    score_params: list = []
    score_parts: list[str] = []
    ands: list[str] = []
    for terms in groups:
        ands.append(_toks_clause(terms, columns, filter_params, score_parts, score_params))
    score = " + ".join(score_parts) if score_parts else "0"
    return " AND ".join(ands), filter_params, score_params, score


def search_fts(
    con,
    q: str,
    *,
    slug: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 40,
) -> list[dict]:
    """PostgreSQL 版 search_fts 行召回（供 search.fts_search 调用）。"""
    match_q = tok.build_match_query(q)
    hits: list[dict] = []
    params: list = []

    def _date_sql() -> str:
        sql = ""
        if date_from or date_to:
            sql += " AND date_end IS NOT NULL AND date_end != ''"
            if date_from:
                sql += " AND date_end >= %s"
                params.append(date_from)
            if date_to:
                sql += " AND date_end <= %s"
                params.append(date_to)
        return sql

    if match_q:
        filt, fp, sp, score_expr = build_toks_filter(match_q, ["toks", "title", "body"])
        if filt:
            sql = f"""
                SELECT issue_slug, section, title, body, date_end,
                       -({score_expr}) AS score
                FROM search_fts
                WHERE {filt}
            """
            # 占位符按 SQL 文本顺序：SELECT 的 score_expr 在前，WHERE 的 filt 在后
            params = list(sp) + list(fp)
            if slug:
                sql += " AND issue_slug = %s"
                params.append(slug)
            sql += _date_sql()
            # score = -(命中词计数)：越小越相关。DESC 会在截断时丢掉最相关行（历史 bug）。
            sql += f" ORDER BY score ASC, date_end DESC NULLS LAST LIMIT %s"
            params.append(max(limit * 3, limit))
            try:
                for r in con.execute(sql, params):
                    hits.append(dict(r))
            except Exception:
                hits = []

    return hits
