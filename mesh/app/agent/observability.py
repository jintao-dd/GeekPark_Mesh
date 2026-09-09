"""Agent 请求可观测 / 审计字段（不改大脑判定）。"""
from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def _answer_status(answer: dict[str, Any]) -> str:
    if answer.get("refused"):
        return "refused"
    bindings = answer.get("claim_bindings") or []
    if any((b or {}).get("status") == "grounded" for b in bindings):
        return "grounded"
    if any((b or {}).get("status") == "weak" for b in bindings):
        return "weak"
    cs = (answer.get("trace") or {}).get("claim_support") or {}
    if isinstance(cs, dict) and cs.get("support"):
        return str(cs.get("support"))
    if answer.get("evidence_refs"):
        return "has_evidence"
    return "unknown"


def attach_observability(
    answer: dict[str, Any],
    *,
    request_id: str,
    envelope: dict[str, Any] | None,
    latency_ms: float,
    error: str = "",
) -> dict[str, Any]:
    """在 harness / HTTP 出口挂 observability，不改 text/evidence 判定。"""
    env = envelope or {}
    trace = answer.get("trace") or {}
    try:
        from app.llm import model_for_task

        model_used = {
            "semantic": model_for_task("semantic"),
            "answer": model_for_task("answer"),
            "sensitive": model_for_task("sensitive"),
        }
    except Exception:
        model_used = {}

    n_hits = None
    # payload 不在 to_dict；从 trace / 文本侧尽力取
    if "n_hits" in trace:
        n_hits = trace.get("n_hits")

    obs = {
        "request_id": request_id,
        "feishu_user": str(env.get("feishu_open_id") or env.get("open_id") or "").strip(),
        "mesh_user_id": env.get("mesh_user_id"),
        "conversation": str(env.get("chat_id") or "").strip() or None,
        "session_id": str(env.get("session_id") or "").strip() or None,
        "channel": str(env.get("channel") or "").strip() or None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_used": model_used,
        "retrieval_n_hits": n_hits,
        "evidence_refs": list(answer.get("evidence_refs") or []),
        "evidence_count": len(answer.get("evidence_refs") or []),
        "answer_status": _answer_status(answer),
        "intent": answer.get("intent"),
        "refused": bool(answer.get("refused")),
        "latency_ms": round(float(latency_ms), 1),
        "llm_used": bool(trace.get("llm_used")) if "llm_used" in trace else None,
        "fingerprint": answer.get("fingerprint") or "",
        "error": error or answer.get("deny_reason") or "",
    }
    out = dict(answer)
    out["request_id"] = request_id
    out["observability"] = obs
    return out


def timed_run(fn, *args, **kwargs) -> tuple[Any, float]:
    t0 = time.perf_counter()
    out = fn(*args, **kwargs)
    return out, (time.perf_counter() - t0) * 1000.0
