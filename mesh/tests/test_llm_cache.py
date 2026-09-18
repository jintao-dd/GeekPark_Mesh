"""LLM 响应缓存单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm_cache


def test_cacheable_tasks():
    assert llm_cache.cacheable_task("answer")
    assert llm_cache.cacheable_task("mouth")
    assert llm_cache.cacheable_task("default")
    assert not llm_cache.cacheable_task("plan")
    assert not llm_cache.cacheable_task("extract")
    assert not llm_cache.cacheable_task("stream")


def test_get_set_cached():
    # 清空缓存
    with llm_cache._CACHE_LOCK:
        llm_cache._CACHE.clear()

    result = {"content": "hello", "usage": {"prompt_tokens": 10}}
    llm_cache.set_cached(
        model="m1", task="answer", system="s", user="u", max_tokens=100, result=result
    )
    cached = llm_cache.get_cached(
        model="m1", task="answer", system="s", user="u", max_tokens=100
    )
    assert cached is not None
    assert cached["content"] == "hello"
    assert cached["usage"]["cache_hit"] is True

    # 不同 user 不命中
    miss = llm_cache.get_cached(
        model="m1", task="answer", system="s", user="u2", max_tokens=100
    )
    assert miss is None

    # 不可缓存任务不命中
    miss2 = llm_cache.set_cached(
        model="m1", task="plan", system="s", user="u", max_tokens=100, result=result
    )
    miss2 = llm_cache.get_cached(
        model="m1", task="plan", system="s", user="u", max_tokens=100
    )
    assert miss2 is None
