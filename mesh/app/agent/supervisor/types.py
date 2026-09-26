"""Supervisor / Worker shared contracts."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


DEFAULT_BUDGET = {
    "plan_steps": 8,
    "tool_calls": 12,
    "wall_time_sec": 60.0,
    "replans": 2,
}


@dataclass
class PlanStep:
    id: str
    worker: str  # org|research|calendar|published|writer
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    parallel_group: str = ""
    optional: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskGraph:
    goal: str
    mode: str = "work"  # speak|work|prepare_write|confirm_write|cancel_write|refuse
    band: str = "ordinary"
    steps: list[PlanStep] = field(default_factory=list)
    write_tool: str = ""
    write_args: dict[str, Any] = field(default_factory=dict)
    refuse_text: str = ""
    speak_hint: str = ""
    budget: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_BUDGET))
    progress: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "mode": self.mode,
            "band": self.band,
            "steps": [s.to_dict() for s in self.steps],
            "write_tool": self.write_tool,
            "write_args": dict(self.write_args),
            "refuse_text": self.refuse_text,
            "speak_hint": self.speak_hint,
            "budget": dict(self.budget),
            "progress": list(self.progress),
        }


@dataclass
class TieredEnvelope:
    step_id: str
    worker: str
    tool: str
    ok: bool
    tier: str  # published|feishu_live|wiki_prior|analysis|system
    text: str = ""
    error: str = ""
    need_replan: bool = False
    replan_reason: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    claim_bindings: list[Any] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SupervisorResult:
    action: str = "speak"
    text: str = ""
    intent: str = "casual"
    llm_used: bool = False
    synthesize_llm_used: bool = False
    model: str | None = None
    tools_called: list[str] = field(default_factory=list)
    claim_bindings: list[Any] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    refused: bool = False
    deny_reason: str = ""
    trace: dict[str, Any] = field(default_factory=dict)
    progress: list[str] = field(default_factory=list)
