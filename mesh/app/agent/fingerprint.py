"""fingerprint（事实版本）≠ trace（路径）。"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .models import AgentContext, PermissionDecision, ToolResult


def build_trace(
    *,
    intent: str,
    tool_id: str | None,
    context: AgentContext,
    identity_status: str,
) -> dict[str, Any]:
    qs = context.query_scope or {}
    view = qs.get("team_focus") or "company"
    return {
        "intent": intent,
        "tool": tool_id,
        "issue": context.issue_ref.slug or None,
        "issue_mode": context.issue_ref.mode,
        "issue_locked": context.issue_ref.locked,
        "view": f"{context.channel}/{view}",
        "scope_key": context.scope_key,
        "identity_status": identity_status,
        "query_scope": dict(qs),
    }


def build_fingerprint(
    *,
    context: AgentContext,
    permission: PermissionDecision,
    tool_result: ToolResult | None,
) -> str:
    """基于 issue + epoch + query_scope + 证据句柄的稳定短指纹。"""
    payload = {
        "issue": context.issue_ref.slug or "",
        "epoch": context.issue_ref.epoch,
        "issue_mode": context.issue_ref.mode,
        "query_scope": permission.query_scope,
        "tool": tool_result.tool_id if tool_result else None,
        "evidence": list(tool_result.evidence_refs) if tool_result else [],
        "published_only": True,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
