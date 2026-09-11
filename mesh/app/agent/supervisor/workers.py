"""Workers: execute tools only; never speak to the user."""
from __future__ import annotations

import logging
from typing import Any, Callable

from .types import PlanStep, TieredEnvelope

log = logging.getLogger("uvicorn.error")

WORKER_FOR_TOOL = {
    "ask.published": "published",
    "ask.relations_summary": "published",
    "context.list_issues": "published",
    "feishu.search": "research",
    "feishu.doc.get": "research",
    "feishu.discuss.summary": "research",
    "feishu.calendar.list": "calendar",
    "feishu.calendar.propose": "calendar",
    "feishu.doc.create": "writer",
    "feishu.calendar.create": "writer",
    "feishu.im.send": "writer",
}

ORG_RESOURCE_TYPES = frozenset({"group", "member", "user", "directory"})

PROGRESS_LABEL = {
    "org": "正在查组织/群成员",
    "research": "正在查飞书资料",
    "calendar": "正在查日历",
    "published": "正在查已上线周报",
    "writer": "正在准备飞书写入",
}


def resolve_worker(tool: str, args: dict[str, Any] | None = None) -> str:
    tool = (tool or "").strip()
    if tool == "feishu.search":
        rt = str((args or {}).get("resource_type") or "").strip().lower()
        if rt in ORG_RESOURCE_TYPES:
            return "org"
        return "research"
    return WORKER_FOR_TOOL.get(tool, "research")


def progress_for(worker: str) -> str:
    return PROGRESS_LABEL.get(worker, f"正在执行 {worker}")


def _tool_args(step: PlanStep) -> dict[str, Any]:
    args = dict(step.args or {})
    nested = args.pop("args", None)
    if isinstance(nested, dict):
        args.update(nested)
    if step.tool.startswith("ask.") or step.tool.startswith("context."):
        if "query" in args:
            return {"query": args.get("query") or ""}
    return args


def _render_intent(tool: str) -> str:
    return {
        "feishu.search": "feishu_search",
        "feishu.doc.get": "feishu_doc_get",
        "feishu.calendar.list": "feishu_calendar_list",
        "feishu.calendar.propose": "feishu_calendar_list",
        "feishu.discuss.summary": "feishu_discuss",
        "ask.published": "ask_published",
        "ask.relations_summary": "ask_relations",
        "context.list_issues": "list_issues",
        "feishu.doc.create": "feishu_write",
        "feishu.calendar.create": "feishu_write",
        "feishu.im.send": "feishu_write",
    }.get((tool or "").strip(), "ask_published")


def run_step(
    step: PlanStep,
    *,
    con: Any,
    identity: Any,
    permission: Any,
    context: Any,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
) -> TieredEnvelope:
    worker = step.worker or resolve_worker(step.tool, step.args)
    try:
        args = _tool_args(step)
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
        if chat_id and step.tool == "feishu.search":
            rt = str(args.get("resource_type") or "")
            if rt in ("member", "group", "message"):
                args.setdefault("chat_id", chat_id)
        result = invoke_tool(step.tool, con, identity, permission, context, args)
        intent = _render_intent(step.tool)
        fact_text, _bindings, _ev = render_tool_result(
            result,
            intent,
            getattr(identity, "status", "") or "",
        )
        ok = bool(getattr(result, "ok", False))
        payload = getattr(result, "payload", None)
        if not isinstance(payload, dict):
            payload = {}
        tier = "feishu_live" if str(step.tool).startswith("feishu.") else "published"
        if payload.get("source_tier"):
            tier = str(payload.get("source_tier"))
        err = str(getattr(result, "error", "") or "")
        text = (fact_text or "").strip()
        if text.startswith("{") and '"action"' in text[:40]:
            text = str(payload.get("answer") or payload.get("snippet") or text)[:2000]
        need_replan = False
        replan_reason = ""
        if not ok and err in ("user_auth_required", "empty", "not_found"):
            need_replan = True
            replan_reason = err
        elif ok and not text and payload.get("empty"):
            need_replan = True
            replan_reason = "empty_result"
        return TieredEnvelope(
            step_id=step.id,
            worker=worker,
            tool=step.tool,
            ok=ok,
            tier=tier,
            text=text[:4000],
            error=err,
            need_replan=need_replan,
            replan_reason=replan_reason,
            payload={
                k: payload.get(k)
                for k in ("empty", "user_auth_required", "meta", "source_tier")
                if k in payload
            },
        )
    except Exception as e:
        log.warning("worker step %s failed: %s", step.id, e)
        return TieredEnvelope(
            step_id=step.id,
            worker=worker,
            tool=step.tool,
            ok=False,
            tier="system",
            error=str(e)[:200],
            need_replan=True,
            replan_reason="exception",
        )
