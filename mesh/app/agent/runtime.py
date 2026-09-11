"""⑦ Agent 编排：Wave1 = Safety → Colleague v3（一张嘴）；可选回退 v2 Controller 路径。

Session Context 只消解指代；事实必须重新 Retrieval / Evidence。
"""
from __future__ import annotations

import os
from typing import Any

from . import context as ctxmod
from . import conversation as conv
from . import colleague_controller as ctrl
from . import fingerprint as fp
from . import identity as idmod
from . import intent as intentmod
from . import permission as permmod
from . import session_state as sstore
from . import tools as toolsmod
from .feishu_reply import enrich_answer_for_display
from .models import (
    DATA_TOOLS,
    AgentAnswer,
    AgentEnvelope,
    ClaimBinding,
)


_REFUSE_BIND = "当前飞书账号尚未绑定 Mesh 用户。请先完成绑定后再提问已上线周报。"
_REFUSE_CONFLICT = (
    "检测到团队归属冲突，无法安全选择查询视角。"
    "请管理员消歧后再问；我仍可提供使用帮助。"
)
_REFUSE_ACL = "这部分信息不在你当前可查看的范围内。"
_REFUSE_DRAFT = "我只能查已上线的内容，不能读草稿、原文或未上线素材。"
_REFUSE_GENERIC = "我还不太确定你的意思。可以说具体一点，或发「帮助」看问法示例。"
_REFUSE_CAPABILITY = (
    "这个我做不了——我只能查已上线周报，不能改权限、发布或写回数据。"
    "权限相关请找管理员；内容问题可以直接问我人和事。"
)


def colleague_v3_enabled() -> bool:
    v = (os.environ.get("MESH_COLLEAGUE_V3") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _colleague_turn(
    *,
    user_text: str,
    session: sstore.SessionContextState,
    mode: str,
    identity=None,
    response_mode: str = "",
) -> tuple[str, dict[str, Any]]:
    """1× Conversation LLM（general / meta / clarify）。不进 Retrieval。"""
    from . import colleague_chat

    hint = ""
    if identity is not None:
        person = getattr(identity, "person", None) or {}
        if isinstance(person, dict):
            name = str(person.get("display") or person.get("name") or "").strip()
            team = str(getattr(identity, "primary_team", None) or "").strip()
            if name:
                hint = f"{name}" + (f"/{team}" if team else "")
    return colleague_chat.reply_colleague(
        user_text,
        session,
        mode=mode,
        identity_hint=hint,
        response_mode=response_mode,
    )


def handle_message(con, envelope: AgentEnvelope) -> AgentAnswer:
    """本地/HTTP Harness 入口。不依赖飞书事件。"""
    identity = idmod.resolve_identity(con, envelope)
    chat_team = ctxmod.chat_team_of(con, envelope.chat_id)
    permission = permmod.decide_permission(
        identity,
        explicit_team=envelope.explicit_team,
        chat_team=chat_team,
    )
    context = ctxmod.assemble_context(con, envelope, identity, permission)

    sk = sstore.session_key_of(
        channel=envelope.channel or identity.channel or "web",
        feishu_open_id=envelope.feishu_open_id or identity.feishu_open_id or "",
        chat_id=envelope.chat_id,
        thread_id=envelope.thread_id,
        session_id=envelope.session_id,
        mesh_user_id=identity.mesh_user_id or envelope.mesh_user_id,
    )
    session = sstore.load(sk)
    session.session_key = sk

    base_kwargs: dict[str, Any] = {
        "identity": identity.to_dict(),
        "permission": permission.to_dict(),
        "context": context.to_dict(),
    }

    def _finish(
        answer: AgentAnswer,
        *,
        route: conv.RouteDecision,
        payload: dict[str, Any] | None = None,
        user_for_state: str = "",
        controller: ctrl.ControllerDecision | None = None,
    ) -> AgentAnswer:
        issue = ""
        if payload and payload.get("issue"):
            issue = str(payload.get("issue") or "")
        if not issue:
            iref = (answer.context or {}).get("issue_ref") or {}
            if isinstance(iref, dict):
                issue = str(iref.get("slug") or "")
        team = str(getattr(identity, "primary_team", None) or "") or ""
        sstore.save(
            conv.update_state_after_turn(
                session,
                route=route,
                user_text=user_for_state or (envelope.text or ""),
                answer_text=str(answer.text or ""),
                intent=answer.intent,
                issue=issue,
                evidence_refs=list(answer.evidence_refs or []),
                team=team,
            )
        )
        answer.trace = dict(answer.trace or {})
        answer.trace["conversation_route"] = route.route
        answer.trace["session_key"] = sk
        answer.trace["session_turn"] = session.turn_id
        if route.rewritten_query:
            answer.trace["rewritten_query"] = route.rewritten_query
        if route.resolved_entity:
            answer.trace["resolved_entity"] = route.resolved_entity
        if controller is not None:
            answer.trace["controller"] = {
                "mode": controller.mode,
                "intent": controller.intent,
                "entities": list(controller.entities or []),
                "topic": controller.topic,
                "needs_grounding": controller.needs_grounding,
                "needs_clarification": controller.needs_clarification,
                "response_mode": controller.response_mode,
                "context_refs": list(controller.context_refs or []),
                "source": controller.source,
                "router_llm_used": bool(controller.router_llm_used),
                "router_model": controller.router_model,
                "confidence": controller.confidence,
            }
            answer.trace["router_llm_used"] = bool(controller.router_llm_used)
        return enrich_answer_for_display(answer, payload=payload)

    if not permission.agent_access:
        route = conv.RouteDecision(route="refuse", intent="refuse", notes="acl")
        return _finish(
            AgentAnswer(
                text=_REFUSE_ACL,
                intent="refuse",
                refused=True,
                deny_reason=permission.deny_reason or "agent_access_denied",
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=fp.build_trace(
                    intent="refuse",
                    tool_id=None,
                    context=context,
                    identity_status=identity.status,
                ),
                **base_kwargs,
            ),
            route=route,
        )

    # —— Wave 1：Safety → Colleague v3（默认开；MESH_COLLEAGUE_V3=0 回退旧路径）——
    if colleague_v3_enabled():
        return _handle_colleague_v3(
            con,
            envelope=envelope,
            identity=identity,
            permission=permission,
            context=context,
            session=session,
            sk=sk,
            base_kwargs=base_kwargs,
            _finish=_finish,
        )

    controller = intentmod.classify_controller(envelope.text, session)
    route = controller.to_route_decision()
    intent = route.intent
    # ACL overlay for data intents（不再二次 decide，避免双倍 Router LLM）
    if intent in ("list_issues", "ask_relations", "ask_published"):
        tool = intentmod.intent_to_tool(intent)
        if not tool or not permmod.tool_allowed(permission, tool):
            intent = "refuse"
            route = conv.RouteDecision(route="refuse", intent="refuse", notes="acl_data")
            controller = ctrl.from_route_decision(route, envelope.text or "", session)

    tool_id = intentmod.intent_to_tool(intent)

    if intent == "whoami":
        return _finish(
            AgentAnswer(
                text=_whoami_text(identity),
                intent="whoami",
                tools_called=[],
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=fp.build_trace(
                    intent="whoami",
                    tool_id=None,
                    context=context,
                    identity_status=identity.status,
                ),
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    # Meta：Conversation LLM（不走 system.help 说明书 / 不进 Retrieval）
    if intent == "help":
        text, chat_meta = _colleague_turn(
            user_text=envelope.text or "",
            session=session,
            mode="meta",
            identity=identity,
            response_mode=controller.response_mode or "explain",
        )
        if not (text or "").strip():
            text = route.casual_text or conv._meta_reply(envelope.text or "")
        tr = fp.build_trace(
            intent="help",
            tool_id=None,
            context=context,
            identity_status=identity.status,
        )
        tr["llm_used"] = bool(chat_meta.get("llm_used"))
        if chat_meta.get("model"):
            tr["model_used"] = chat_meta.get("model")
        tr["colleague_response_mode"] = chat_meta.get("response_mode")
        return _finish(
            AgentAnswer(
                text=text,
                intent="help",
                tools_called=[],
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=tr,
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    # General conversation：必须 1× Answer LLM，不进 Published Retrieval
    if intent == "casual":
        text, chat_meta = _colleague_turn(
            user_text=envelope.text or "",
            session=session,
            mode="chat",
            identity=identity,
            response_mode=controller.response_mode or "conversational",
        )
        if not (text or "").strip():
            text = route.casual_text or conv._casual_reply(envelope.text or "")
        tr = fp.build_trace(
            intent="casual",
            tool_id=None,
            context=context,
            identity_status=identity.status,
        )
        tr["llm_used"] = bool(chat_meta.get("llm_used"))
        if chat_meta.get("model"):
            tr["model_used"] = chat_meta.get("model")
        tr["colleague_response_mode"] = chat_meta.get("response_mode")
        return _finish(
            AgentAnswer(
                text=text,
                intent="casual",
                tools_called=[],
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=tr,
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    # Clarify：Stage 2A 走 Conversation LLM（response_mode=clarify），模板仅回落
    if intent == "clarify":
        text, chat_meta = _colleague_turn(
            user_text=envelope.text or "",
            session=session,
            mode="clarify",
            identity=identity,
            response_mode=controller.response_mode or "clarify",
        )
        if not (text or "").strip():
            text = route.clarify_text or "能再说具体一点吗？"
        tr = fp.build_trace(
            intent="clarify",
            tool_id=None,
            context=context,
            identity_status=identity.status,
        )
        tr["llm_used"] = bool(chat_meta.get("llm_used"))
        if chat_meta.get("model"):
            tr["model_used"] = chat_meta.get("model")
        tr["colleague_response_mode"] = chat_meta.get("response_mode")
        return _finish(
            AgentAnswer(
                text=text,
                intent="clarify",
                tools_called=[],
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=tr,
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    if intent == "refuse" or tool_id is None:
        text = _refuse_text(identity.status, envelope.text, permission.deny_reason)
        return _finish(
            AgentAnswer(
                text=text,
                intent="refuse",
                refused=True,
                deny_reason=permission.deny_reason or "refuse",
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=fp.build_trace(
                    intent="refuse",
                    tool_id=None,
                    context=context,
                    identity_status=identity.status,
                ),
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    if not permmod.tool_allowed(permission, tool_id):
        if permmod.tool_allowed(permission, "system.help") and tool_id != "system.help":
            help_r = toolsmod.invoke_tool(
                "system.help", con, identity, permission, context, {}
            )
            return _finish(
                AgentAnswer(
                    text=_REFUSE_ACL
                    + "\n\n"
                    + str((help_r.payload or {}).get("help") or ""),
                    intent="refuse",
                    tools_called=["system.help"],
                    refused=True,
                    deny_reason="acl_denied",
                    fingerprint=fp.build_fingerprint(
                        context=context, permission=permission, tool_result=help_r
                    ),
                    trace=fp.build_trace(
                        intent="refuse",
                        tool_id="system.help",
                        context=context,
                        identity_status=identity.status,
                    ),
                    **base_kwargs,
                ),
                route=route,
                controller=controller,
            )
        return _finish(
            AgentAnswer(
                text=_REFUSE_ACL,
                intent="refuse",
                refused=True,
                deny_reason="acl_denied",
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace=fp.build_trace(
                    intent="refuse",
                    tool_id=None,
                    context=context,
                    identity_status=identity.status,
                ),
                **base_kwargs,
            ),
            route=route,
            controller=controller,
        )

    data_count = 1 if tool_id in DATA_TOOLS else 0
    assert data_count <= 1

    # Follow-up：用 rewrite 后的问句检索（不是上一轮答案）
    ask_q = (route.rewritten_query or envelope.text or "").strip()
    args: dict[str, Any] = {"q": ask_q}
    result = toolsmod.invoke_tool(
        tool_id, con, identity, permission, context, args
    )

    text, bindings, evidence = _render(result, intent, identity.status)
    trace = fp.build_trace(
        intent=intent,
        tool_id=tool_id,
        context=context,
        identity_status=identity.status,
    )
    payload = result.payload if isinstance(result.payload, dict) else {}
    if "llm_used" in payload:
        trace["llm_used"] = bool(payload.get("llm_used"))
    if payload.get("temporal"):
        trace["temporal"] = payload.get("temporal")
    if "n_hits" in payload:
        trace["n_hits"] = payload.get("n_hits")
    if payload.get("claim_support"):
        trace["claim_support"] = payload.get("claim_support")
    answer = AgentAnswer(
        text=text,
        intent=intent,
        tools_called=[tool_id],
        fingerprint=fp.build_fingerprint(
            context=context, permission=permission, tool_result=result
        ),
        trace=trace,
        claim_bindings=bindings,
        evidence_refs=evidence,
        refused=bool(result.denied),
        deny_reason=result.error if result.denied else "",
        **base_kwargs,
    )
    return _finish(answer, route=route, payload=payload, user_for_state=ask_q, controller=controller)


def _handle_colleague_v3(
    con,
    *,
    envelope: AgentEnvelope,
    identity,
    permission,
    context,
    session: sstore.SessionContextState,
    sk: str,
    base_kwargs: dict[str, Any],
    _finish,
) -> AgentAnswer:
    """SafetyGate → Colleague v3；旁路 Controller。"""
    from . import colleague_v3
    from . import safety_gate

    text_in = envelope.text or ""

    # 确定性 whoami：绑定状态必须准，不交给模型瞎编
    qn = conv.normalize_query(text_in)
    if conv._WHOAMI.search(qn) and not conv._META.search(qn):
        route = conv.RouteDecision(route="meta", intent="whoami", notes="v3_whoami")
        return _finish(
            AgentAnswer(
                text=_whoami_text(identity),
                intent="whoami",
                tools_called=[],
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace={
                    **fp.build_trace(
                        intent="whoami",
                        tool_id=None,
                        context=context,
                        identity_status=identity.status,
                    ),
                    "colleague_v3": True,
                    "router_llm_used": False,
                },
                **base_kwargs,
            ),
            route=route,
        )

    kind, _notes = safety_gate.check_refuse(text_in)
    if kind == "draft":
        route = conv.RouteDecision(route="refuse", intent="refuse", notes="draft_raw")
        return _finish(
            AgentAnswer(
                text=_REFUSE_DRAFT,
                intent="refuse",
                refused=True,
                deny_reason="draft_raw",
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace={
                    **fp.build_trace(
                        intent="refuse",
                        tool_id=None,
                        context=context,
                        identity_status=identity.status,
                    ),
                    "colleague_v3": True,
                    "safety_gate": "draft",
                    "router_llm_used": False,
                },
                **base_kwargs,
            ),
            route=route,
        )
    if kind == "capability":
        route = conv.RouteDecision(route="refuse", intent="refuse", notes="capability")
        return _finish(
            AgentAnswer(
                text=_REFUSE_CAPABILITY,
                intent="refuse",
                refused=True,
                deny_reason="capability_boundary",
                fingerprint=fp.build_fingerprint(
                    context=context, permission=permission, tool_result=None
                ),
                trace={
                    **fp.build_trace(
                        intent="refuse",
                        tool_id=None,
                        context=context,
                        identity_status=identity.status,
                    ),
                    "colleague_v3": True,
                    "safety_gate": "capability",
                    "router_llm_used": False,
                },
                **base_kwargs,
            ),
            route=route,
        )

    result = colleague_v3.handle(
        con=con,
        user_text=text_in,
        identity=identity,
        permission=permission,
        context=context,
        session=session,
        invoke_tool=toolsmod.invoke_tool,
        render_tool_result=_render,
    )

    # 映射 route 供 Session 更新
    if result.action == "ask" or result.intent in (
        "ask_published",
        "ask_relations",
        "feishu_search",
        "feishu_doc_get",
        "feishu_calendar_list",
        "feishu_discuss",
        "feishu_write",
        "list_issues",
    ):
        if result.intent == "ask_published":
            route_name = "ask"
        elif result.intent == "ask_relations":
            route_name = "relations"
        elif result.intent in (
            "feishu_search",
            "feishu_doc_get",
            "feishu_calendar_list",
            "feishu_discuss",
            "feishu_write",
        ):
            route_name = "feishu"
        else:
            route_name = "list"
        route = conv.RouteDecision(
            route=route_name,
            intent=result.intent,
            rewritten_query=str((result.trace or {}).get("ask_query") or text_in),
            notes="colleague_v3_ask",
        )
    elif result.action == "refuse" or result.refused:
        route = conv.RouteDecision(route="refuse", intent="refuse", notes="colleague_v3")
    else:
        route = conv.RouteDecision(
            route="general_conversation",
            intent="casual",
            notes="colleague_v3_speak",
        )

    tr = fp.build_trace(
        intent=result.intent,
        tool_id=(result.tools_called[0] if result.tools_called else None),
        context=context,
        identity_status=identity.status,
    )
    tr["colleague_v3"] = True
    tr["router_llm_used"] = False  # 无独立 Controller
    tr["llm_used"] = bool(result.llm_used or result.synthesize_llm_used)
    tr["colleague_action"] = result.action
    if result.model:
        tr["model_used"] = result.model
    tr.update(result.trace or {})
    if result.payload.get("temporal"):
        tr["temporal"] = result.payload.get("temporal")
    if "n_hits" in (result.payload or {}):
        tr["n_hits"] = result.payload.get("n_hits")
    if result.payload.get("claim_support"):
        tr["claim_support"] = result.payload.get("claim_support")

    answer = AgentAnswer(
        text=result.text,
        intent=result.intent,
        tools_called=list(result.tools_called or []),
        fingerprint=fp.build_fingerprint(
            context=context, permission=permission, tool_result=None
        ),
        trace=tr,
        claim_bindings=list(result.claim_bindings or []),
        evidence_refs=list(result.evidence_refs or []),
        refused=bool(result.refused),
        deny_reason=result.deny_reason or "",
        **base_kwargs,
    )
    user_for_state = str((result.trace or {}).get("ask_query") or text_in)
    return _finish(
        answer,
        route=route,
        payload=result.payload if result.action == "ask" else None,
        user_for_state=user_for_state,
    )


def _whoami_text(identity) -> str:
    person = getattr(identity, "person", None) or {}
    if not isinstance(person, dict):
        person = {}
    display = (
        str(getattr(identity, "display_hint", None) or "").strip()
        or str(person.get("display") or person.get("name") or "").strip()
    )
    team = str(getattr(identity, "primary_team", None) or "").strip()
    if display:
        bit = f"你是 {display}"
        if team:
            bit += f"（{team}）"
        return bit + "。想查周报直接说人名或公司就行。"
    return "我这边还没认出你的名字。想查周报的话，直接说人名或公司就行。"


def _refuse_text(status: str, text: str, deny_reason: str) -> str:
    from . import conversation as convmod

    if convmod._CAPABILITY_REFUSE.search(text or ""):
        return _REFUSE_CAPABILITY
    if intentmod._DRAFT_RAW.search(text or ""):  # noqa: SLF001
        if any(
            k in (text or "").lower()
            for k in ("草稿", "draft", "原文", "raw", "未上线", "查库")
        ) or "草稿" in (text or "") or "未上线" in (text or "") or "原文" in (text or ""):
            return _REFUSE_DRAFT
    if status in ("unlinked", "anonymous_web", "ambiguous", "open_id_mismatch"):
        return _REFUSE_BIND
    if status == "bound_team_conflict":
        return _REFUSE_CONFLICT
    if deny_reason:
        return _REFUSE_ACL
    return _REFUSE_GENERIC


def _render(
    result,
    intent: str,
    identity_status: str,
) -> tuple[str, list[ClaimBinding], list[str]]:
    if result.denied:
        err = str(result.error or "")
        if "identity" in err or "acl" in err:
            return (_REFUSE_ACL, [], [])
        return (
            "刚才没查成功，你可以再试一次；或换个说法问问。",
            [],
            [],
        )
    payload = result.payload or {}
    visible_bindings = [
        b for b in (result.claim_bindings or []) if b.status != "unsupported"
    ]
    if intent == "help":
        return str(payload.get("help") or "帮助"), visible_bindings, list(result.evidence_refs or [])
    if intent == "list_issues":
        issues = payload.get("issues") or []
        if not issues:
            return "当前没有已上线期次。", visible_bindings, list(result.evidence_refs or [])
        lines = [f"- {i.get('slug')} {i.get('period_label') or ''}".strip() for i in issues[:30]]
        return "已上线期次：\n" + "\n".join(lines), visible_bindings, list(result.evidence_refs or [])
    if intent == "feishu_search":
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            if payload.get("empty") or not result.ok:
                answer = "飞书文档这边这轮没查到相关结果。"
            else:
                answer = "飞书文档这边这轮没查到相关结果。"
        return answer, visible_bindings, list(result.evidence_refs or [])
    if intent in (
        "feishu_doc_get",
        "feishu_calendar_list",
        "feishu_discuss",
        "feishu_write",
    ):
        answer = str(payload.get("answer") or "").strip()
        if not answer:
            answer = "飞书这边这轮没查到相关结果。" if intent != "feishu_write" else "写入未完成。"
        return answer, visible_bindings, list(result.evidence_refs or [])
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        answer = "周报侧这轮没有可直接对齐的条目（不等于飞书侧也没有）。"
    return answer, visible_bindings, list(result.evidence_refs or [])
