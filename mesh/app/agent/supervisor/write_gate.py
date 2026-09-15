"""WriteGate — Supervisor owns pending_write; Writer Worker executes on confirm.

不再 monkey-patch colleague_v3._decide。确认后走 workers.run_step(worker=writer)。
"""
from __future__ import annotations

import re
from typing import Any, Callable

from ..session_state import SessionContextState
from ..tool_contract import FEISHU_WRITE_TOOLS
from . import workers
from .mouth import sanitize
from .types import PlanStep, SupervisorResult, TaskGraph

_CONFIRM_RE = re.compile(
    r"^(好的?|可以|要|行|嗯+|确认|创建吧|发吧|写吧|就这样|ok|yes|y)\s*[。.!！]*$",
    re.I,
)
_CANCEL_RE = re.compile(
    r"^(不要|别|取消|算了|先不用|no|n)\s*[。.!！]*$",
    re.I,
)


def _clean(text: str) -> str:
    from ..conversation import normalize_query

    return normalize_query(text or "")


def hard_confirm_or_cancel(user_text: str, session: SessionContextState) -> str | None:
    qn = _clean(user_text)
    pending = getattr(session, "pending_write", None)
    if not isinstance(pending, dict) or not pending.get("tool"):
        return None
    if qn and _CONFIRM_RE.match(qn):
        return "confirm_write"
    if qn and _CANCEL_RE.match(qn):
        return "cancel_write"
    return None


def validate_write_tool(tool: str) -> bool:
    return (tool or "").strip() in FEISHU_WRITE_TOOLS


def _sync_helpers():
    """Reuse calendar/doc draft helpers from colleague_v3 without owning Decide."""
    from .. import colleague_v3 as v3

    return v3


def _fill_write_chat_args(tool: str, args: dict[str, Any], context: Any) -> dict[str, Any]:
    out = dict(args or {})
    chat_id = ""
    if context is not None:
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
    if chat_id:
        out.setdefault("chat_id", chat_id)
        if tool == "feishu.im.send":
            out.setdefault("receive_id", chat_id)
            out.setdefault("receive_id_type", "chat_id")
    return out

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
    v3 = _sync_helpers()
    q = _clean(user_text)
    v3._sync_pending_from_store(session, context, identity)
    out = SupervisorResult(
        action="speak",
        intent="feishu_write",
        llm_used=False,
        trace={"write_gate": True, "supervisor": True, "supervisor_owned_write": True},
    )

    if mode == "cancel_write":
        v3._clear_pending(session, context, identity)
        v3._clear_active_goal(session)
        out.text = sanitize("好，已取消，不会写入飞书。")
        out.intent = "casual"
        out.trace["write_cancelled"] = True
        return out

    if mode == "confirm_write":
        pending0 = session.pending_write if isinstance(session.pending_write, dict) else None
        if (not pending0 or not pending0.get("tool")) and re.search(
            r"日程|日历|开会|约", q or ""
        ):
            mode = "prepare_write"
            graph = TaskGraph(
                goal=graph.goal or q[:200],
                mode="prepare_write",
                write_tool="feishu.calendar.create",
                write_args={},
            )
            out.trace["confirm_without_pending_reroute"] = True
        else:
            return _confirm_via_writer(
                con=con,
                q=q,
                identity=identity,
                permission=permission,
                context=context,
                session=session,
                invoke_tool=invoke_tool,
                render_tool_result=render_tool_result,
                company_block=company_block,
                out=out,
                v3=v3,
            )

    if mode == "prepare_write":
        return _prepare(
            graph=graph,
            con=con,
            q=q,
            identity=identity,
            permission=permission,
            context=context,
            session=session,
            company_block=company_block,
            out=out,
            v3=v3,
        )

    out.text = sanitize("写入路径异常。")
    out.intent = "casual"
    return out


def _prepare(
    *,
    graph: TaskGraph,
    con: Any,
    q: str,
    identity: Any,
    permission: Any,
    context: Any,
    session: SessionContextState,
    company_block: str,
    out: SupervisorResult,
    v3: Any,
) -> SupervisorResult:
    from .. import feishu_hands
    from .. import permission as permmod

    tool = str(graph.write_tool or "feishu.doc.create").strip()
    if tool not in FEISHU_WRITE_TOOLS:
        out.intent = "casual"
        out.text = sanitize("这个写入我还不支持。你可以让我先整理成文，你自己贴到飞书。")
        return out
    if not feishu_hands.hands_enabled():
        out.intent = "casual"
        out.text = sanitize("飞书 Hands 还没开，我可以先帮你把文稿整理好，但不能直接写入飞书。")
        return out
    if not permmod.tool_allowed(permission, tool) and feishu_hands.write_enabled():
        out.intent = "casual"
        out.text = sanitize("你当前没有这项飞书写入权限。")
        return out

    args = dict(graph.write_args or {})
    args = _fill_write_chat_args(tool, args, context)
    if tool == "feishu.doc.create" and not str(args.get("content") or "").strip():
        want_empty = bool(
            re.search(r"空的?文档|空文档", q)
            and not re.search(r"介绍|关于|写|内容|说明", q)
        )
        if want_empty:
            args["content"] = ""
            args.setdefault("title", "未命名文档")
        else:
            from . import mouth as mouthmod

            draft, smeta = mouthmod.speak(
                "用户要创建飞书文档。请按用户原话直接写出可粘贴进文档的完整正文"
                "（不要寒暄、不要问要不要创建、不要输出 JSON）。\n"
                f"用户原话：{q}",
                identity,
                session,
                company_block=company_block,
            )
            out.llm_used = bool(smeta.get("llm_used"))
            if smeta.get("model"):
                out.model = smeta.get("model")
            args["content"] = draft
            if not str(args.get("title") or "").strip():
                args["title"] = (q[:32] or "Mesh 整理").strip()
    if tool == "feishu.im.send" and not str(args.get("text") or "").strip():
        args["text"] = q
    if tool == "feishu.calendar.create":
        args["title"] = v3._guess_calendar_title(q, args)
        start = str(args.get("start") or "").strip()
        end = str(args.get("end") or "").strip()
        if (not start or start == "None") or (not end or end == "None"):
            ps, pe = v3._parse_calendar_range(q)
            if ps and pe:
                args["start"], args["end"] = ps, pe
        start = str(args.get("start") or "").strip()
        end = str(args.get("end") or "").strip()
        if not start or not end or start == "None" or end == "None":
            out.text = sanitize(
                f"想帮你建「{args.get('title') or '日程'}」，但还缺具体时间。"
                "请说清几点到几点，例如「明天下午两点到三点」。"
            )
            out.trace["source_tier"] = "feishu_live"
            return out
        from .. import feishu_user_auth as uauth

        oid = str(getattr(identity, "feishu_open_id", None) or "").strip()
        if not uauth.resolve_uat(open_id=oid):
            session.pending_write = {"tool": tool, "args": args}
            v3._persist_pending(session, context, identity)
            v3._set_active_goal(
                session,
                summary=f"写入 {tool}：{args.get('title')}",
                family="feishu_write",
                tool=tool,
                utterance=q,
            )
            guide = uauth.auth_guide_text(open_id=oid, capability="以你的名义创建日程")
            out.text = sanitize(
                v3._prepare_write_text(tool, args)
                + "\n\n"
                + guide
                + "\n\n授权完成后直接回复「确认」即可创建。"
            )
            out.trace["pending_write"] = tool
            out.trace["source_tier"] = "feishu_live"
            out.trace["user_auth_required"] = True
            out.progress = [workers.progress_for("writer")]
            return out

    session.pending_write = {"tool": tool, "args": args}
    v3._persist_pending(session, context, identity)
    v3._set_active_goal(
        session,
        summary=f"写入 {tool}" + (f"：{args.get('title')}" if args.get("title") else ""),
        family="feishu_write",
        tool=tool,
        utterance=q,
    )
    preview = v3._prepare_write_text(tool, args)
    if not feishu_hands.write_enabled():
        preview += "\n\n（当前环境写入开关关闭：即使你确认也不会真正写入，可先当预览。）"
    out.text = sanitize(preview)
    out.trace["pending_write"] = tool
    out.trace["active_goal"] = session.active_goal
    out.trace["source_tier"] = "feishu_live"
    out.progress = [workers.progress_for("writer")]
    return out


def _confirm_via_writer(
    *,
    con: Any,
    q: str,
    identity: Any,
    permission: Any,
    context: Any,
    session: SessionContextState,
    invoke_tool: Callable[..., Any],
    render_tool_result: Callable[..., Any],
    company_block: str,
    out: SupervisorResult,
    v3: Any,
) -> SupervisorResult:
    from .. import feishu_hands
    from .. import permission as permmod

    pending = session.pending_write if isinstance(session.pending_write, dict) else None
    if not pending or not pending.get("tool"):
        out.intent = "casual"
        out.text = sanitize("我这边没有待确认的写入。你要创建文档、发消息还是建日程？")
        return out

    tool = str(pending.get("tool") or "")
    args = v3._merge_confirm_args(tool, dict(pending.get("args") or {}), q)
    args["confirmed"] = True
    args = _fill_write_chat_args(tool, args, context)

    if not feishu_hands.write_enabled():
        v3._set_last_block(session, code="hands_off", tool=tool, message="write_disabled")
        out.text = sanitize(
            "写入开关关着（MESH_FEISHU_HANDS_WRITE），我不能真正写入飞书。预览还在，打开开关后再说「确认」。"
        )
        return out
    if not permmod.tool_allowed(permission, tool):
        v3._clear_pending(session, context, identity)
        v3._set_last_block(session, code="scope_denied", tool=tool, message="tool_acl")
        out.intent = "casual"
        out.text = sanitize("你当前没有这项飞书写入权限。")
        return out

    step = PlanStep(
        id="writer_confirm",
        worker="writer",
        tool=tool,
        args=args,
    )
    env = workers.run_step(
        step,
        con=con,
        identity=identity,
        permission=permission,
        context=context,
        invoke_tool=invoke_tool,
        render_tool_result=render_tool_result,
        prior=[],
        user_text=q,
    )
    out.tools_called = [tool]
    out.progress = [workers.progress_for("writer")]
    out.payload = dict(env.payload or {})
    out.trace["writer_envelope"] = env.to_dict()
    out.trace["source_tier"] = "feishu_live"

    if (not env.ok) and (
        str(env.error or "") == "user_auth_required"
        or (isinstance(out.payload, dict) and out.payload.get("user_auth_required"))
    ):
        from .. import feishu_user_auth as uauth

        guide = str((out.payload or {}).get("auth_text") or "").strip()
        if not guide:
            guide = uauth.auth_guide_text(
                open_id=str(getattr(identity, "feishu_open_id", None) or ""),
                capability="以你的名义创建日程",
            )
        v3._set_last_block(session, code="user_auth_required", tool=tool, message="need_uat")
        out.text = sanitize(guide + "\n\n授权完成后回复「确认」，我会按刚才的预览写入。")
        out.trace["write_confirmed"] = tool
        out.trace["user_auth_required"] = True
        return out

    v3._clear_pending(session, context, identity)
    if env.ok:
        v3._clear_active_goal(session)
        v3._clear_last_block(session)
        from . import mouth as mouthmod

        spoken, smeta = mouthmod.speak(
            f"写入已完成，用一两句告诉用户结果。工具事实：\n{(env.text or '')[:800]}",
            identity,
            session,
            company_block=company_block,
            hint="feishu_write_done",
        )
        out.synthesize_llm_used = bool(smeta.get("llm_used"))
        out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
        if smeta.get("model"):
            out.model = smeta.get("model")
        out.action = "ask"
        out.text = sanitize(spoken or env.text or "写入已完成。")
        meta = out.payload.get("meta") if isinstance(out.payload.get("meta"), dict) else {}
        url = str(meta.get("url") or "").strip()
        if url and url not in out.text:
            out.text = out.text.rstrip() + f"\n\n文档地址：{url}"
        out.trace["write_confirmed"] = tool
        return out

    err = str(env.error or "")
    out.action = "speak"
    out.text = sanitize(
        f"写入没成功：{err or '未知错误'}。预览已清掉，需要的话再说一次要写什么。"
    )
    out.trace["write_failed"] = err
    return out
