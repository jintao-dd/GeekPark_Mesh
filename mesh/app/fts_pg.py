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


def _toks_clause(terms: list[str], columns: list[str], params: list, score_parts: list[str]) -> str:
    """一组 term OR → SQL 子句。"""
    ors: list[str] = []
    for term in terms:
        pat = f"%{term}%"
        col_ors = []
        for col in columns:
            col_ors.append(f"{col} LIKE %s")
            params.append(pat)
        ors.append(f"({' OR '.join(col_ors)})")
        score_parts.append(f"CASE WHEN toks LIKE %s THEN 1 ELSE 0 END")
        params.append(pat)
    return f"({' OR '.join(ors)})"


def build_toks_filter(match_q: str, columns: list[str]) -> tuple[str, list, str]:
    """
    返回 (WHERE 片段, params, score_expr)。
    score_expr 为匹配 term 计数（越大越好，取负用于 ORDER BY）。
    """
    groups = _match_groups(match_q)
    if not groups:
        return "", [], "0"
    params: list = []
    score_parts: list[str] = []
    ands: list[str] = []
    for terms in groups:
        ands.append(_toks_clause(terms, columns, params, score_parts))
    score = " + ".join(score_parts) if score_parts else "0"
    return " AND ".join(ands), params, score


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
        filt, fp, score_expr = build_toks_filter(match_q, ["toks", "title", "body"])
        if filt:
            sql = f"""
                SELECT issue_slug, section, title, body, date_end,
                       -({score_expr}) AS score
                FROM search_fts
                WHERE {filt}
            """
            params = fp + params
            sql += _date_sql()
            sql += f" ORDER BY score DESC, date_end DESC NULLS LAST LIMIT %s"
            params.append(max(limit * 3, limit))
            try:
                for r in con.execute(sql, params):
                    hits.append(dict(r))
            except Exception:
                hits = []

    return hits
