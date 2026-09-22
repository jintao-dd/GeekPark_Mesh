"""向量 embedding：OpenAI 兼容接口 + 本地 cosine 检索（零额外依赖）。

Runtime 纪律（与质量冻结对齐）：
  - 默认 Vector / Embedding = OFF（须显式 MESH_EMBED_ENABLED=1 或 MESH_VECTOR_ENABLED=1）
  - OFF 时 Ask/Agent 热路径不得调用 embed API；不靠「调用失败再 fallback」
"""
from __future__ import annotations

import heapq
import json
import math
import re
import threading
from typing import Any, Literal

import requests

from .providers.base import env

_HTTP = requests.Session()

# 向量矩阵进程内缓存：941 条 × 4096 维 JSON 约 86MB，每次查询重新解析会白花数秒。
# 按 (model, 语料指纹) 缓存一次解析结果，语料变更时重建。
_VEC_CACHE: dict[str, Any] = {"key": "", "rows": [], "norms": []}
_VEC_CACHE_LOCK = threading.Lock()


def _corpus_key(con: Any, model: str) -> str:
    try:
        row = con.execute(
            "SELECT COUNT(*) AS c, COALESCE(SUM(LENGTH(vector_json)),0) AS n "
            "FROM chunk_embeddings WHERE model=?",
            (model,),
        ).fetchone()
        return f"{model}:{int(row['c'])}:{int(row['n'])}"
    except Exception:
        return ""


def _cached_matrix(con: Any, model: str, sql: str, params: list[Any]) -> tuple[list, list]:
    """返回 (rows, norms)；rows 为 (chunk_meta_tuple, vector)。"""
    key = _corpus_key(con, model)
    with _VEC_CACHE_LOCK:
        if key and _VEC_CACHE["key"] == key:
            return _VEC_CACHE["rows"], _VEC_CACHE["norms"]
    rows: list[Any] = []
    norms: list[float] = []
    for r in con.execute(sql, params):
        try:
            vec = json.loads(r["vector_json"])
        except Exception:
            continue
        if not isinstance(vec, list) or not vec:
            continue
        rows.append((r, vec))
        norms.append(math.sqrt(sum(x * x for x in vec)))
    with _VEC_CACHE_LOCK:
        if key:
            _VEC_CACHE["key"] = key
            _VEC_CACHE["rows"] = rows
            _VEC_CACHE["norms"] = norms
    return rows, norms

RetrievalMode = Literal["structured", "lexical", "hybrid"]


def enabled() -> bool:
    """向量检索 / query embedding 开关。默认 OFF（与 Vector frozen OFF 一致）。"""
    v = (env("MESH_EMBED_ENABLED") or env("MESH_VECTOR_ENABLED") or "0").lower()
    return v not in ("0", "false", "no", "off", "")


def vector_retrieval_enabled() -> bool:
    """热路径是否允许 vector / embed_one。等同 enabled ∧ configured keys。"""
    return is_configured()


def retrieval_execution_mode(*, structured_candidate: bool) -> RetrievalMode:
    """本次请求实际执行模式（非 Planner，仅 Runtime 决策）。

    - structured：结构化候选
    - hybrid：显式打开 Vector 且已配置
    - lexical：默认 / Vector OFF
    """
    if structured_candidate:
        return "structured"
    if vector_retrieval_enabled():
        return "hybrid"
    return "lexical"


def model_name() -> str:
    return env("MESH_EMBED_MODEL") or env("MESH_EMBEDDING_MODEL") or "text-embedding-3-small"


def _api_key() -> str:
    embed_url = env("MESH_EMBED_BASE_URL")
    embed_key = env("MESH_EMBED_API_KEY")
    if embed_url:
        return embed_key or ""
    return embed_key or env("MESH_LLM_API_KEY") or ""


def _base_urls() -> list[str]:
    raw = env("MESH_EMBED_BASE_URL")
    if not raw:
        return [(env("MESH_LLM_BASE_URL") or "https://api.openai.com/v1").rstrip("/")]
    return [u.strip().rstrip("/") for u in raw.split(",") if u.strip()]


def _base_url() -> str:
    urls = _base_urls()
    return urls[0] if urls else "https://api.openai.com/v1"


def is_configured() -> bool:
    if not enabled():
        return False
    if env("MESH_EMBED_BASE_URL"):
        return bool(env("MESH_EMBED_API_KEY"))
    return bool(_api_key())


def batch_size() -> int:
    try:
        return max(1, min(128, int(env("MESH_EMBED_BATCH") or "32")))
    except ValueError:
        return 32


_last_error: str = ""
_call_count: int = 0


def last_error() -> str:
    return _last_error


def call_count() -> int:
    """进程内 embed_texts / embed_one 入口调用次数（含 OFF 时误入热路径）。"""
    return _call_count


def reset_call_count() -> None:
    global _call_count
    _call_count = 0


def _post_embeddings(url: str, payload: dict) -> requests.Response:
    return _HTTP.post(
        url,
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    """批量 embedding；失败返回空列表并记录 last_error。"""
    global _last_error, _call_count
    _last_error = ""
    if texts:
        # 任何非空入口都计数：Vector OFF 下热路径误入可被自检 / 性能报告抓住
        _call_count += 1
    if not texts or not is_configured():
        if not is_configured():
            _last_error = "embedding not configured (MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL)"
        return []
    payload = {"model": model_name(), "input": texts}
    errors: list[str] = []
    for base in _base_urls():
        url = base + "/embeddings"
        try:
            r = _post_embeddings(url, payload)
            if r.status_code >= 400:
                body = (r.text or "")[:500]
                msg = f"HTTP {r.status_code} {url}: {body}"
                errors.append(msg)
                continue
            data = r.json().get("data") or []
            out: list[list[float]] = []
            for row in sorted(data, key=lambda x: x.get("index", 0)):
                vec = row.get("embedding")
                if isinstance(vec, list):
                    out.append([float(x) for x in vec])
            if len(out) != len(texts):
                msg = f"expected {len(texts)} vectors, got {len(out)} from {url}"
                errors.append(msg)
                continue
            return out
        except Exception as e:
            errors.append(f"{url}: {e}")
    _last_error = errors[-1] if errors else "embedding request failed"
    print(f"[mesh] embed error: {_last_error}", flush=True)
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
               c.owner_team, c.stype, c.item_id, c.source_label, c.meta_json, e.vector_json
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
    top_k = max(limit * 4, limit)
    rows, norms = _cached_matrix(con, model, sql, params)
    qn = math.sqrt(sum(x * x for x in query_vec))
    if qn <= 0:
        return []
    heap: list[tuple[float, dict]] = []
    for (r, vec), rn in zip(rows, norms):
        if rn <= 0 or len(vec) != len(query_vec):
            continue
        dot = sum(x * y for x, y in zip(query_vec, vec))
        sc = dot / (qn * rn)
        if sc <= 0.05:
            continue
        meta = {}
        try:
            raw_meta = r["meta_json"]
            if raw_meta:
                meta = json.loads(raw_meta)
        except Exception:
            meta = {}
        hit = {
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
            "layer": r["layer"] or "",
            "meta": meta,
        }
        if len(heap) < top_k:
            heapq.heappush(heap, (-sc, hit))
        elif sc > -heap[0][0]:
            heapq.heapreplace(heap, (-sc, hit))
    hits = [h for _, h in sorted(heap, key=lambda x: x[0])]
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
