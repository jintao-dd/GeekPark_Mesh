"""Verify TieredEnvelopes; decide if Supervisor should replan."""
from __future__ import annotations

from typing import Any

from .types import TaskGraph, TieredEnvelope

_OPTIONAL_SOURCES = ("crm.search", "ask.published", "feishu.search")


def verify(
    envelopes: list[TieredEnvelope],
    *,
    graph: TaskGraph,
) -> dict[str, Any]:
    ok_n = sum(1 for e in envelopes if e.ok)
    need = [e for e in envelopes if e.need_replan]
    empty_n = sum(1 for e in envelopes if e.need_replan and e.replan_reason == "empty_result")
    tiers = sorted({e.tier for e in envelopes if e.ok and e.tier})
    used = {str(e.tool) for e in envelopes}
    unused = [t for t in _OPTIONAL_SOURCES if t not in used]
    cross = "published" in tiers and "feishu_live" in tiers
    want_replan = False
    reason = ""
    if envelopes and ok_n == 0:
        want_replan = True
        reason = "all_failed"
    elif need and ok_n < max(1, len(envelopes) // 2):
        want_replan = True
        reason = need[0].replan_reason or "partial_need_replan"
    elif empty_n and unused:
        want_replan = True
        reason = "empty_with_unused_sources"
    observations: list[dict[str, Any]] = [
        {
            "step_id": e.step_id,
            "worker": e.worker,
            "tool": e.tool,
            "ok": e.ok,
            "tier": e.tier,
            "error": e.error[:120],
            "need_replan": e.need_replan,
            "empty": bool((e.payload or {}).get("empty")) or e.replan_reason == "empty_result",
            "snippet": (e.text or "")[:180],
        }
        for e in envelopes[:8]
    ]
    if unused:
        observations.append(
            {
                "unused_sources": unused,
                "note": "这些源这轮没用过；若目标可能落在它们上面，换源再查，不要空辩解。",
            }
        )
    return {
        "ok_count": ok_n,
        "total": len(envelopes),
        "tiers": tiers,
        "cross_bucket": cross,
        "unused_sources": unused,
        "want_replan": want_replan,
        "replan_reason": reason,
        "observations": observations,
    }
