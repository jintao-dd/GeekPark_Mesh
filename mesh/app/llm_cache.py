"""LLM 调用结果缓存（进程内 · TTL · LRU）。

设计前提
--------
- 当前 Mesh 部署在单容器/单进程（uvicorn worker=1），进程内缓存即可命中同进程内重复调用。
- tmesh/prod 环境均未运行 Redis；新增 Redis 会带来部署、运维、故障面成本。
- 因此 Batch 3 先用进程内缓存，命中高频重复 LLM 请求；后续若横向扩展为多 worker，再引入 Redis。

缓存策略
--------
- key = sha256(model | task | system | user | max_tokens)[:32]
- TTL = 5 分钟（环境变量 MESH_LLM_CACHE_TTL_S，默认 300）
- 容量上限 = 256 条（环境变量 MESH_LLM_CACHE_MAX，默认 256），LRU 淘汰
- 只缓存非流式、低风险任务：answer / mouth / default
- 不缓存：stream、json_mode（抽取/决策等对重试敏感）、write_gate、supervisor plan 等
- 命中时 usage 会多带 cache_hit=true，便于监控真实 LLM 调用量
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any

_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()


def _cache_ttl_s() -> int:
    try:
        return int(os.environ.get("MESH_LLM_CACHE_TTL_S") or 300)
    except ValueError:
        return 300


def _cache_max() -> int:
    try:
        return max(16, int(os.environ.get("MESH_LLM_CACHE_MAX") or 256))
    except ValueError:
        return 256


def _cache_key(*, model: str, task: str, system: str, user: str, max_tokens: int) -> str:
    blob = json.dumps(
        {
            "model": model or "",
            "task": task or "default",
            "system": system or "",
            "user": user or "",
            "max_tokens": max_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def _gc() -> None:
    now = time.monotonic()
    ttl = _cache_ttl_s()
    stale = [k for k, v in _CACHE.items() if now - v.get("_at", 0) > ttl]
    for k in stale:
        _CACHE.pop(k, None)
    # 超过容量时按 _at 最老淘汰
    max_size = _cache_max()
    if len(_CACHE) > max_size:
        sorted_keys = sorted(_CACHE.keys(), key=lambda k: _CACHE[k].get("_at", 0))
        for k in sorted_keys[: len(_CACHE) - max_size]:
            _CACHE.pop(k, None)


def cacheable_task(task: str) -> bool:
    """仅对幂等、低风险任务启用缓存。"""
    return (task or "default").strip().lower() in {"answer", "mouth", "default"}


def get_cached(
    *,
    model: str,
    task: str,
    system: str,
    user: str,
    max_tokens: int,
) -> dict[str, Any] | None:
    if not cacheable_task(task):
        return None
    key = _cache_key(model=model, task=task, system=system, user=user, max_tokens=max_tokens)
    with _CACHE_LOCK:
        ent = _CACHE.get(key)
        if not ent:
            return None
        if time.monotonic() - ent.get("_at", 0) > _cache_ttl_s():
            _CACHE.pop(key, None)
            return None
        ent["_at"] = time.monotonic()  # 刷新 LRU
        result = dict(ent.get("result") or {})
    # 标记命中，不影响原结果结构
    usage = dict(result.get("usage") or {})
    usage["cache_hit"] = True
    result["usage"] = usage
    return result


def set_cached(
    *,
    model: str,
    task: str,
    system: str,
    user: str,
    max_tokens: int,
    result: dict[str, Any],
) -> None:
    if not cacheable_task(task):
        return
    key = _cache_key(model=model, task=task, system=system, user=user, max_tokens=max_tokens)
    with _CACHE_LOCK:
        _CACHE[key] = {"result": result, "_at": time.monotonic()}
        _gc()
