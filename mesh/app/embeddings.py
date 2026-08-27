"""向量 embedding：OpenAI 兼容接口 + 本地 cosine 检索（零额外依赖）。"""
from __future__ import annotations

import json
import math
import os
import re
from typing import Any

import requests

from .providers.base import env


def enabled() -> bool:
    v = (env("MESH_EMBED_ENABLED") or env("MESH_VECTOR_ENABLED") or "1").lower()
    return v not in ("0", "false", "no", "off")


def model_name() -> str:
    return env("MESH_EMBED_MODEL") or env("MESH_EMBEDDING_MODEL") or "text-embedding-3-small"


def _api_key() -> str:
    return env("MESH_EMBED_API_KEY") or env("MESH_LLM_API_KEY")


def _base_url() -> str:
    return (env("MESH_EMBED_BASE_URL") or env("MESH_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")


def is_configured() -> bool:
    return enabled() and bool(_api_key())


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量 embedding；失败返回空列表。"""
    if not texts or not is_configured():
        return []
    payload = {"model": model_name(), "input": texts}
    try:
        r = requests.post(
            _base_url() + "/embeddings",
            headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if r.status_code >= 400:
            return []
        data = r.json().get("data") or []
        out: list[list[float]] = []
        for row in sorted(data, key=lambda x: x.get("index", 0)):
            vec = row.get("embedding")
            if isinstance(vec, list):
                out.append([float(x) for x in vec])
        return out
    except Exception:
        return []


def embed_one(text: str) -> list[float]:
    rows = embed_texts([(text or "")[:8000]])
    return rows[0] if rows else []


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def vector_search(
    con,
    query_vec: list[float],
    *,
    slug: str = "",
    team: str = "",
    date_from: str | None = None,
    date_to: str | None = None,
    limit: int = 24,
) -> list[dict]:
    """对 chunk_embeddings 做 brute-force cosine（语料百～千级足够）。"""
    if not query_vec:
        return []
    model = model_name()
    params: list[Any] = [model]
    sql = """
        SELECT c.chunk_id, c.issue_slug, c.date_end, c.layer, c.section, c.title, c.body,
               c.owner_team, c.stype, c.item_id, c.source_label, e.vector_json
        FROM chunk_index c
        JOIN chunk_embeddings e ON e.chunk_id = c.chunk_id AND e.model = ?
        WHERE 1=1
    """
    if slug:
        sql += " AND c.issue_slug = ?"
        params.append(slug)
    if team:
        sql += " AND c.owner_team = ?"
        params.append(team)
    if date_from:
        sql += " AND c.date_end >= ?"
        params.append(date_from)
    if date_to:
        sql += " AND c.date_end <= ?"
        params.append(date_to)
    hits: list[dict] = []
    for r in con.execute(sql, params):
        try:
            vec = json.loads(r["vector_json"])
        except Exception:
            continue
        if not isinstance(vec, list):
            continue
        sc = cosine(query_vec, vec)
        if sc <= 0.05:
            continue
        hits.append({
            "issue_slug": r["issue_slug"],
            "section": r["section"] or "抽取条目",
            "title": r["title"] or "",
            "body": r["body"] or "",
            "date_end": r["date_end"] or "",
            "score": -sc,
            "item_id": r["item_id"],
            "owner_team": r["owner_team"] or "",
            "stype": r["stype"] or "",
            "source": "vector",
            "chunk_id": r["chunk_id"],
        })
    hits.sort(key=lambda x: x["score"])
    return hits[:limit]


# 轻量 query 扩展：中英文别名 + 引号内专名
_ALIAS = {
    "embodied": "具身智能",
    "humanoid": "人形机器人",
    "agent": "智能体",
    "llm": "大模型",
    "具身": "具身智能",
}


def expand_query(q: str) -> list[str]:
    q = (q or "").strip()
    if not q:
        return []
    out = [q]
    ql = q.lower()
    for en, zh in _ALIAS.items():
        if en in ql:
            variant = re.sub(re.escape(en), zh, q, flags=re.IGNORECASE)
            if variant not in out:
                out.append(variant)
            if zh not in out:
                out.append(f"{q} {zh}")
    quoted = re.findall(r"[「『\"']([^」』\"']{2,40})[」』\"']", q)
    for t in quoted:
        if t not in out:
            out.append(t)
    return list(dict.fromkeys(out))[:4]
