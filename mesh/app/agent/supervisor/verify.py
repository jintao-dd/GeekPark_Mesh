"""Verify TieredEnvelopes; decide if Supervisor should replan."""
from __future__ import annotations

from typing import Any

from .types import TaskGraph, TieredEnvelope


def verify(
    envelopes: list[TieredEnvelope],
    *,
    graph: TaskGraph,
) -> dict[str, Any]:
    ok_n = sum(1 for e in envelopes if e.ok)
    need = [e for e in envelopes if e.need_replan]
    tiers = sorted({e.tier for e in envelopes if e.ok and e.tier})
    cross = "published" in tiers and "feishu_live" in tiers
    # 复杂任务却几乎全空 → 建议 replan
    want_replan = False
    reason = ""
    if graph.band in ("medium", "complex") and ok_n == 0 and envelopes:
        want_replan = True
        reason = "all_failed"
    elif need and ok_n < max(1, len(envelopes) // 2):
        want_replan = True
        reason = need[0].replan_reason or "partial_need_replan"
    return {
        "ok_count": ok_n,
        "total": len(envelopes),
        "tiers": tiers,
        "cross_bucket": cross,
        "want_replan": want_replan,
        "replan_reason": reason,
        "observations": [
            {
                "step_id": e.step_id,
                "worker": e.worker,
                "tool": e.tool,
                "ok": e.ok,
                "tier": e.tier,
                "error": e.error[:120],
                "need_replan": e.need_replan,
                "snippet": (e.text or "")[:180],
            }
            for e in envelopes[:8]
        ],
    }
