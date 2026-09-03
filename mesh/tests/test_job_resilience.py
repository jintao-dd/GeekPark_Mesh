"""Pipeline / Preview 容错：分类、重试、局部跳过。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.job_resilience import (
    ResilienceReport,
    classify_error,
    run_with_retries,
    try_unit,
)
from app.providers.base import LLMError


def test_classify_retryable_and_fatal():
    assert classify_error(LLMError("模型接口返回 524：timeout")) == "retryable"
    assert classify_error(LLMError("模型返回的 JSON 无法解析：Expecting")) == "retryable"
    assert classify_error(RuntimeError("抽取排队超时：busy")) == "retryable"
    assert classify_error(RuntimeError("本期已上线，不能直接生成预览")) == "fatal"
    assert classify_error(RuntimeError("未配置 MESH_LLM_API_KEY")) == "fatal"


def test_run_with_retries_succeeds_after_transient():
    n = {"i": 0}

    def flaky():
        n["i"] += 1
        if n["i"] < 3:
            raise LLMError("模型接口返回 524：x")
        return "ok"

    rep = ResilienceReport()
    assert run_with_retries(flaky, unit="u", kind="source", report=rep, sleep_sec=0) == "ok"
    assert n["i"] == 3
    assert rep.retry_total == 2
    assert any(a.action == "ok" for a in rep.attempts)


def test_try_unit_skips_after_retries_exhausted():
    def always_fail():
        raise LLMError("模型返回的 JSON 无法解析：bad")

    rep = ResilienceReport()
    out = try_unit(always_fail, unit="#1 src", kind="source", report=rep, sleep_sec=0)
    assert out is None
    assert "#1 src" in rep.skipped
    assert rep.retry_total == 2
    d = rep.to_dict()
    assert d["retry_total"] == 2
    assert d["skipped"] == ["#1 src"]
    assert any(a["action"] == "skip" for a in d["attempts"])


def test_fatal_does_not_retry():
    n = {"i": 0}

    def fatal():
        n["i"] += 1
        raise RuntimeError("未配置 MESH_LLM_API_KEY")

    rep = ResilienceReport()
    try:
        run_with_retries(fatal, unit="u", report=rep, sleep_sec=0)
        assert False, "expected raise"
    except RuntimeError:
        pass
    assert n["i"] == 1
    assert rep.retry_total == 0
