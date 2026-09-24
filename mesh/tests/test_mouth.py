"""Mouth 成文层：source 诚实性 + latest guard。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.supervisor import mouth
from app.agent.supervisor.types import TaskGraph, TieredEnvelope


def _env(tier: str, text: str, ok: bool = True, payload=None) -> TieredEnvelope:
    return TieredEnvelope(
        step_id="s1",
        worker=tier,
        tool="ask.published" if tier == "published" else "crm.search",
        ok=ok,
        tier=tier,
        text=text,
        payload=payload or {},
    )


def test_materials_block_lists_missing_sources():
    """标注未返回材料的源，防止 LLM 编造「某源没查出」。"""
    env = _env("published", "- 张岩：拟观察进前沿社")
    out = mouth.materials_block([env], planned_tools=["ask.published", "crm.search"])
    assert "### 本轮未返回材料的源" in out
    assert "硅谷 CRM（思琪侧）" in out


def test_materials_block_no_missing_if_no_step():
    """没计划某源时，不标注缺失。"""
    env = _env("published", "- foo")
    out = mouth.materials_block([env], planned_tools=["ask.published"])
    assert "### 本轮未返回材料的源" not in out


def test_latest_guard_triggers_on_negative_answer(monkeypatch):
    """问最新 + 答案写没有 + evidence 非空 → 触发重抽。"""
    calls: list[tuple[str, str]] = []

    def fake_llm(system, user, *, max_tokens=4000, json_mode=False, task="default"):
        calls.append((system, user))
        if len(calls) == 1:
            return "Notta.ai 没有最新动态。"
        return "Notta.ai 张岩拟观察进前沿社（2026-09-15），这是最新动态。"

    monkeypatch.setattr("app.llm.call", fake_llm)
    monkeypatch.setattr("app.llm.model_for_task", lambda *a, **k: "mock")

    text, meta, _ = mouth.synthesize_work(
        user_text="Notta.ai 的最新消息",
        envelopes=[_env("published", "- Notta.ai 创始人张岩拟观察进前沿社（2026-09-15）")],
        graph=TaskGraph(goal="Notta.ai 的最新消息", mode="work"),
    )
    assert len(calls) == 2, "应触发一次重抽"
    assert "硬性纠偏" in calls[1][0]
    assert "拟观察进前沿社" in text
    assert meta.get("source") == "llm_mouth_retry_latest"


def test_latest_guard_falls_back_when_retry_still_negative(monkeypatch):
    """重抽仍然是否定答案，则回落规则分栏（规则分栏会列出证据）。"""
    def fake_llm(system, user, *, max_tokens=4000, json_mode=False, task="default"):
        return "没有最新消息。"

    monkeypatch.setattr("app.llm.call", fake_llm)
    monkeypatch.setattr("app.llm.model_for_task", lambda *a, **k: "mock")

    text, meta, _ = mouth.synthesize_work(
        user_text="Notta.ai 的最新消息",
        envelopes=[_env("published", "- Notta.ai 创始人张岩拟观察进前沿社")],
        graph=TaskGraph(goal="Notta.ai 的最新消息", mode="work"),
    )
    assert "张岩" in text  # 规则分栏会保留证据
    assert meta.get("source") == "rule_columns_latest_guard"


def test_latest_guard_not_triggered_for_non_latest_question(monkeypatch):
    """非最新类问题，不触发 guard。"""
    calls: list[tuple[str, str]] = []

    def fake_llm(system, user, *, max_tokens=4000, json_mode=False, task="default"):
        calls.append((system, user))
        return "没有。"

    monkeypatch.setattr("app.llm.call", fake_llm)
    monkeypatch.setattr("app.llm.model_for_task", lambda *a, **k: "mock")

    text, meta, _ = mouth.synthesize_work(
        user_text="Notta.ai 有多少人",
        envelopes=[_env("published", "- 张岩")],
        graph=TaskGraph(goal="Notta.ai 有多少人", mode="work"),
    )
    assert len(calls) == 1
    assert meta.get("source") == "llm_mouth"
