"""⑦ Agent v1 单轮编排。"""
from __future__ import annotations

from typing import Any

from . import context as ctxmod
from . import fingerprint as fp
from . import identity as idmod
from . import intent as intentmod
from . import permission as permmod
from . import tools as toolsmod
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
_REFUSE_ACL = "当前身份无权执行该操作。"
_REFUSE_DRAFT = "只能查询已上线（Published）内容，不能读取草稿、原文或未上线素材。"
_REFUSE_GENERIC = "无法可靠理解该请求，未调用数据工具。可发送「帮助」查看用法。"


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

    base_kwargs: dict[str, Any] = {
        "identity": identity.to_dict(),
        "permission": permission.to_dict(),
        "context": context.to_dict(),
    }

    if not permission.agent_access:
        return AgentAnswer(
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
        )

    intent = intentmod.rule_classify_intent(envelope.text, context, permission)
    tool_id = intentmod.intent_to_tool(intent)

    if intent == "refuse" or tool_id is None:
        text = _refuse_text(identity.status, envelope.text, permission.deny_reason)
        return AgentAnswer(
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
        )

    if not permmod.tool_allowed(permission, tool_id):
        # 可降级 help
        if permmod.tool_allowed(permission, "system.help") and tool_id != "system.help":
            help_r = toolsmod.invoke_tool(
                "system.help", con, identity, permission, context, {}
            )
            return AgentAnswer(
                text=_refuse_text(identity.status, envelope.text, "acl_denied")
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
            )
        return AgentAnswer(
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
        )

    # 硬约束：至多 1 个数据 Tool
    data_count = 1 if tool_id in DATA_TOOLS else 0
    assert data_count <= 1

    args: dict[str, Any] = {"q": (envelope.text or "").strip()}
    result = toolsmod.invoke_tool(
        tool_id, con, identity, permission, context, args
    )

    # 禁止因空/差结果再打第二个数据 Tool（硬约束：此处直接 render）
    text, bindings, evidence = _render(result, intent, identity.status)
    return AgentAnswer(
        text=text,
        intent=intent,
        tools_called=[tool_id],
        fingerprint=fp.build_fingerprint(
            context=context, permission=permission, tool_result=result
        ),
        trace=fp.build_trace(
            intent=intent,
            tool_id=tool_id,
            context=context,
            identity_status=identity.status,
        ),
        claim_bindings=bindings,
        evidence_refs=evidence,
        refused=bool(result.denied),
        deny_reason=result.error if result.denied else "",
        **base_kwargs,
    )


def _refuse_text(status: str, text: str, deny_reason: str) -> str:
    from . import intent as intentmod

    if intentmod._DRAFT_RAW.search(text or ""):  # noqa: SLF001 — shared pattern
        if "草稿" in (text or "") or "draft" in (text or "").lower() or "原文" in (text or "") or "raw" in (text or "").lower() or "未上线" in (text or "") or "查库" in (text or ""):
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
        return (
            f"{_REFUSE_ACL}（{result.error}）",
            [],
            [],
        )
    payload = result.payload or {}
    # unsupported 不上屏为事实句
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
    answer = str(payload.get("answer") or "").strip()
    if not answer:
        # 空结果也不二次 Tool
        answer = "未在已上线语料中找到可引用依据。"
    return answer, visible_bindings, list(result.evidence_refs or [])
