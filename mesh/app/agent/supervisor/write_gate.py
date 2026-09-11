"""WriteGate — pending_write under Supervisor; Workers do not confirm themselves."""
from __future__ import annotations

from typing import Any, Callable

from ..session_state import SessionContextState
from ..tool_contract import FEISHU_WRITE_TOOLS
from .mouth import sanitize
from .types import SupervisorResult, TaskGraph


def hard_confirm_or_cancel(user_text: str, session: SessionContextState) -> str | None:
    from .. import colleague_v3 as v3

    qn = v3._clean_user_text(user_text)
    pending = getattr(session, "pending_write", None)
    if not isinstance(pending, dict) or not pending.get("tool"):
        return None
    if v3._looks_like_confirm(qn):
        return "confirm_write"
    if v3._looks_like_cancel(qn):
        return "cancel_write"
    return None


def run_write(
    *,
    mode: str,
    graph: TaskGraph,
    con: Any,
    user_text: str,
    identity: Any,
    permission: Any,
    context: Any,
    session: SessionContextState,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
    company_block: str = "",
) -> SupervisorResult:
    """Delegate write state machine to colleague_v3 helpers via forced decision."""
    from .. import colleague_v3 as v3

    decision: dict[str, Any]
    if mode == "prepare_write":
        decision = {
            "action": "prepare_write",
            "tool": graph.write_tool or "feishu.doc.create",
            "args": dict(graph.write_args or {}),
        }
    elif mode == "confirm_write":
        decision = {"action": "confirm_write"}
    elif mode == "cancel_write":
        decision = {"action": "cancel_write"}
    else:
        return SupervisorResult(action="speak", text="写入路径异常。", intent="casual")

    # Force decision path: reuse v3 handle internals by monkey-patching _decide once
    forced_meta = {"llm_used": False, "model": None, "forced_by_supervisor": True}

    def _forced_decide(*_a, **_k):
        return decision, forced_meta

    orig = v3._decide
    v3._decide = _forced_decide  # type: ignore
    try:
        out = v3.handle(
            con=con,
            user_text=user_text,
            identity=identity,
            permission=permission,
            context=context,
            session=session,
            invoke_tool=invoke_tool,
            render_tool_result=render_tool_result,
        )
    finally:
        v3._decide = orig  # type: ignore

    return SupervisorResult(
        action=out.action,
        text=sanitize(out.text),
        intent=out.intent,
        llm_used=bool(out.llm_used),
        synthesize_llm_used=bool(out.synthesize_llm_used),
        model=out.model,
        tools_called=list(out.tools_called or []),
        claim_bindings=list(out.claim_bindings or []),
        evidence_refs=list(out.evidence_refs or []),
        payload=dict(out.payload or {}),
        refused=bool(out.refused),
        deny_reason=out.deny_reason or "",
        trace={**(out.trace or {}), "write_gate": True, "supervisor": True},
    )


def validate_write_tool(tool: str) -> bool:
    return (tool or "").strip() in FEISHU_WRITE_TOOLS
