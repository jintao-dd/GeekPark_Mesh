"""OpenAI 兼容 provider：思考型模型需关闭 thinking，否则推理 token 吃光 completion。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import openai_compat_provider as ocp


def test_thinking_disabled_for_deepseek_flash(monkeypatch):
    monkeypatch.delenv("MESH_LLM_DISABLE_THINKING", raising=False)
    assert ocp._thinking_disabled("deepseek/deepseek-v4.1-flash") is True
    assert ocp._thinking_disabled("deepseek/deepseek-v4-flash") is True


def test_thinking_not_disabled_for_opus(monkeypatch):
    monkeypatch.delenv("MESH_LLM_DISABLE_THINKING", raising=False)
    assert ocp._thinking_disabled("anthropic/claude-4.8-opus") is False
    assert ocp._thinking_disabled("") is False


def test_thinking_override_env(monkeypatch):
    monkeypatch.setenv("MESH_LLM_DISABLE_THINKING", "my-model-x")
    assert ocp._thinking_disabled("my-model-x-v2") is True
    # 覆盖后 deepseek 不再默认关闭
    assert ocp._thinking_disabled("deepseek/deepseek-v4.1-flash") is False


def test_request_payload_includes_thinking_disable(monkeypatch):
    monkeypatch.delenv("MESH_LLM_DISABLE_THINKING", raising=False)
    monkeypatch.setenv("MESH_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("MESH_LLM_BASE_URL", "https://example.invalid/v1")

    p = ocp.OpenAICompatProvider()
    p.model = "deepseek/deepseek-v4.1-flash"
    payload = p._request_payload("sys", "user", 160, stream=True)
    assert payload.get("thinking") == {"type": "disabled"}
    assert payload["model"] == "deepseek/deepseek-v4.1-flash"

    p.model = "anthropic/claude-4.8-opus"
    payload2 = p._request_payload("sys", "user", 160, stream=True)
    assert "thinking" not in payload2
