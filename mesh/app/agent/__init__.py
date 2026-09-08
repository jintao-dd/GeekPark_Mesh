"""Agent v1：受控单轮编排（不依赖飞书）。

流水线：Identity → Permission → Context → Rule Intent → ≤1 Tool → Answer。
架构见 docs/AGENT_ARCHITECTURE_V1.md；⑧ 飞书只接线，不扩本包能力。
"""
from __future__ import annotations

from .runtime import handle_message
from .models import AgentAnswer, AgentEnvelope

__all__ = ["handle_message", "AgentAnswer", "AgentEnvelope"]
