"""规则 Intent：经 Colleague Controller；不可靠 → refuse。禁止 LLM 自由选 Tool。"""
from __future__ import annotations

from . import colleague_controller as ctrl
from . import conversation as conv
from .models import AgentContext, PermissionDecision
from .permission import tool_allowed
from .session_state import SessionContextState

# re-export patterns used by runtime refuse text
_DRAFT_RAW = conv._DRAFT_RAW  # noqa: F401
normalize_query = conv.normalize_query


def rule_classify_intent(
    text: str,
    context: AgentContext,
    permission: PermissionDecision,
    *,
    session: SessionContextState | None = None,
) -> str:
    """优先级：Colleague Controller → ACL。"""
    decision = ctrl.decide(text, session)
    intent = decision.to_route_decision().intent

    if intent in ("help", "whoami", "casual", "clarify", "refuse"):
        return intent

    if intent == "list_issues":
        return "list_issues" if tool_allowed(permission, "context.list_issues") else "refuse"

    if intent == "ask_relations":
        return "ask_relations" if tool_allowed(permission, "ask.relations_summary") else "refuse"

    if intent == "ask_published":
        return "ask_published" if tool_allowed(permission, "ask.published") else "refuse"

    return "refuse"


def classify_route(
    text: str,
    session: SessionContextState | None = None,
) -> conv.RouteDecision:
    return ctrl.decide(text, session).to_route_decision()


def classify_controller(
    text: str,
    session: SessionContextState | None = None,
) -> ctrl.ControllerDecision:
    return ctrl.decide(text, session)


def intent_to_tool(intent: str) -> str | None:
    return conv.intent_to_tool(intent)
