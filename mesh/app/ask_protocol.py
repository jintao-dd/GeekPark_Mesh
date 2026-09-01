"""Ask Analysis SSE 协议常量与 step 事件构造（契约 v1）。

见 docs/ASK_ANALYSIS_CONTRACT.md。
"""
from __future__ import annotations

from typing import Any

STEPS = ("route", "retrieve", "group", "source", "cross", "verify")
STATUSES = ("running", "completed", "skipped", "error", "timeout")


def step_event(
    step: str,
    status: str,
    *,
    analysis_id: str,
    message: str = "",
    **extra: Any,
) -> dict[str, Any]:
    """构造符合契约的 step 事件。非法 step/status 会落到最接近的合法值。"""
    s = step if step in STEPS else "retrieve"
    st = status if status in STATUSES else "completed"
    ev: dict[str, Any] = {
        "type": "step",
        "step": s,
        "status": st,
        "analysis_id": analysis_id,
        "report_id": analysis_id,
        "message": message or "",
    }
    for k, v in extra.items():
        if v is not None:
            ev[k] = v
    return ev


def source_finish_status(report: dict | None) -> str:
    """根据分源报告标记映射契约 status。"""
    r = report or {}
    err = str(r.get("_error") or "")
    if err == "timeout" or (r.get("_fallback") and err == "timeout"):
        return "timeout"
    if err and not r.get("facts") and not r.get("evidence"):
        return "error"
    if r.get("_fallback") and err and err != "timeout":
        return "timeout" if "timeout" in err.lower() else "completed"
    return "completed"


def cross_finish_status(cross: dict | None) -> str:
    c = cross or {}
    if c.get("_fallback"):
        err = str(c.get("_error") or "")
        if err == "timeout" or "timeout" in err.lower():
            return "timeout"
        return "completed"
    return "completed"
