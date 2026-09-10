"""Colleague v3 — 单一主体：一张嘴听+说；Ask 只是工具。

Wave 1：旁路 Controller；注入 Identity；speak | ask_* | refuse。
禁止：Decide Agent、Multi-Agent、response_mode 预算中轴。
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .session_state import SessionContextState

log = logging.getLogger("uvicorn.error")

_TOOLS = {
    "ask.published",
    "ask.relations_summary",
    "context.list_issues",
}

_SYSTEM = """你是 GeekPark（极客公园）内部的 AI 同事「Mesh」，在飞书里和员工说话。

你是一个人，不是路由系统、不是客服、不是检索框。
用自然同事口吻回应；可以有情绪和观点；不要客服腔、不要「很抱歉给您带来不便」、不要每轮追问还需要什么、不要强行 emoji。
除非用户问你是谁/能干什么，不要自我介绍或背能力清单。

公司事实（某人/公司在已上线周报里怎样、跟谁聊过、关系、期次列表）必须通过工具查，不能编造。
观点用「我觉得」可以；但必须说清这是看法不是周报事实。

你每次只输出一个 JSON 对象（不要 markdown）：
{"action":"speak","text":"..."}
{"action":"ask","tool":"ask.published|ask.relations_summary|context.list_issues","query":"..."}
{"action":"refuse","text":"..."}

选择：
- 闲聊、吐槽、情绪、观点、润色改写、澄清问题、自我反馈 → speak
- 明确要查已上线周报事实 → ask（query 写成完整可检索问句）
- 用户要改权限/发布/读草稿原文 → refuse（Safety 也会拦；你仍可 refuse）

ask.published：人物/公司进展、跟谁聊过、最近怎么样（有明确对象时）
ask.relations_summary：团队/实体之间有哪些关系
context.list_issues：有哪些已上线期次
对象不清时用 speak 问一句，不要盲目 ask。
"""


@dataclass
class ColleagueV3Result:
    action: str = "speak"  # speak|ask|refuse
    text: str = ""
    tool_id: str = ""
    query: str = ""
    intent: str = "casual"
    llm_used: bool = False
    synthesize_llm_used: bool = False
    model: str | None = None
    tools_called: list[str] = field(default_factory=list)
    claim_bindings: list[Any] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    payload: dict[str, Any] = field(default_factory=dict)
    refused: bool = False
    deny_reason: str = ""
    trace: dict[str, Any] = field(default_factory=dict)


def _identity_block(identity: Any) -> str:
    if identity is None:
        return "对方身份：未知（尚未绑定）。不确定时不要假装认识，可自然说明。"
    person = getattr(identity, "person", None) or {}
    if not isinstance(person, dict):
        person = {}
    display = (
        str(getattr(identity, "display_hint", None) or "").strip()
        or str(person.get("display") or person.get("name") or "").strip()
    )
    team = str(getattr(identity, "primary_team", None) or "").strip()
    status = str(getattr(identity, "status", None) or "").strip()
    role = str(getattr(identity, "mesh_role", None) or "").strip()
    lines = [f"绑定状态：{status or 'unknown'}"]
    if display:
        lines.append(f"对方姓名：{display}")
    else:
        lines.append("对方姓名：尚未确认")
    if team:
        lines.append(f"对方团队（canonical_team）：{team}")
    else:
        lines.append("对方团队：未知或不唯一")
    if role:
        lines.append(f"Mesh 角色：{role}")
    lines.append("用这些信息自然对话（例如知道对方团队时可以说「你们…」），但不要每句点名汇报身份。")
    return "\n".join(lines)


def _history_block(state: SessionContextState | None) -> str:
    if not state or not state.recent_turns:
        return ""
    lines = []
    for t in (state.recent_turns or [])[-8:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        lines.append(f"{role}：{t.get('text') or ''}")
    return "最近对话：\n" + "\n".join(lines)


def _parse_decision(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if not s:
        return {"action": "speak", "text": "嗯，我在听。你接着说。"}
    # strip fence
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        data = json.loads(s)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            data = json.loads(m.group(0))
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return {"action": "speak", "text": s[:1200]}


def _decide(user_text: str, identity: Any, state: SessionContextState | None) -> tuple[dict[str, Any], dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": ""}
    system = _SYSTEM + "\n\n## 对方\n" + _identity_block(identity)
    hist = _history_block(state)
    user = (hist + "\n\n" if hist else "") + f"用户：{(user_text or '').strip()}\nJSON："
    try:
        from .. import llm

        raw = llm.call(
            system,
            user,
            max_tokens=700,
            json_mode=True,
            task="answer",
        )
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        return _parse_decision(str(raw or "")), meta
    except Exception as e:
        log.warning("colleague_v3 decide failed: %s", e)
        meta["error"] = str(e)[:120]
        return {
            "action": "speak",
            "text": "刚才卡了一下。你再说一遍，或直接丢个人名/公司名我帮你查周报。",
        }, meta


def _synthesize(
    user_text: str,
    fact_text: str,
    identity: Any,
    state: SessionContextState | None,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None}
    system = (
        "你是 GeekPark 内部同事 Mesh。下面是已从已上线周报查到的事实材料。"
        "用同一张同事的嘴转述给用户：自然、清楚、可保留关键证据感；"
        "不要编造材料没有的事实；不要客服腔；不要自我介绍。"
        "若材料说查不到，就坦诚说没查到。\n\n"
        + _identity_block(identity)
    )
    hist = _history_block(state)
    user = (
        (hist + "\n\n" if hist else "")
        + f"用户问：{(user_text or '').strip()}\n\n事实材料：\n{(fact_text or '').strip()}\n\nMesh："
    )
    try:
        from .. import llm

        out = llm.call(system, user, max_tokens=900, json_mode=False, task="answer")
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        text = str(out or "").strip()
        return (text or fact_text), meta
    except Exception as e:
        log.warning("colleague_v3 synthesize failed: %s", e)
        return fact_text, meta


def _intent_for(action: str, tool_id: str) -> str:
    if action == "refuse":
        return "refuse"
    if action == "ask":
        return {
            "ask.published": "ask_published",
            "ask.relations_summary": "ask_relations",
            "context.list_issues": "list_issues",
        }.get(tool_id, "ask_published")
    return "casual"


def handle(
    *,
    con,
    user_text: str,
    identity,
    permission,
    context,
    session: SessionContextState,
    invoke_tool,
    render_tool_result,
) -> ColleagueV3Result:
    """主回合：Decide(+可选 Ask + 可选合成)。"""
    q = (user_text or "").strip()
    decision, dmeta = _decide(q, identity, session)
    action = str(decision.get("action") or "speak").strip().lower()
    out = ColleagueV3Result(
        llm_used=bool(dmeta.get("llm_used")),
        model=dmeta.get("model"),
        trace={
            "colleague_v3": True,
            "decide_error": dmeta.get("error") or "",
        },
    )
    out.trace["decision"] = {
        "action": action,
        "tool": str(decision.get("tool") or ""),
        "query": str(decision.get("query") or "")[:200],
    }

    if action == "refuse":
        out.action = "refuse"
        out.text = str(decision.get("text") or "这个我做不了。").strip()
        out.intent = "refuse"
        out.refused = True
        out.deny_reason = "colleague_refuse"
        return out

    if action == "ask":
        tool = str(decision.get("tool") or "ask.published").strip()
        if tool not in _TOOLS:
            tool = "ask.published"
        from . import permission as permmod

        if not permmod.tool_allowed(permission, tool):
            out.action = "speak"
            out.text = "这块不在你当前能看的范围里；换个已上线、你有权限的人或事试试。"
            out.intent = "casual"
            return out
        query = str(decision.get("query") or q).strip() or q
        result = invoke_tool(tool, con, identity, permission, context, {"q": query})
        out.tools_called = [tool]
        intent = _intent_for("ask", tool)
        out.intent = intent
        fact_text, bindings, evidence = render_tool_result(result, intent, identity.status)
        out.claim_bindings = list(bindings or [])
        out.evidence_refs = list(evidence or [])
        out.payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
        if getattr(result, "denied", False):
            out.refused = True
            out.deny_reason = str(getattr(result, "error", "") or "ask_denied")
            out.text = fact_text
            out.action = "ask"
            return out
        # 同声线合成
        spoken, smeta = _synthesize(q, fact_text, identity, session)
        out.synthesize_llm_used = bool(smeta.get("llm_used"))
        out.text = spoken
        out.action = "ask"
        out.trace["synthesize"] = bool(smeta.get("llm_used"))
        out.trace["ask_query"] = query
        return out

    # speak（默认）
    out.action = "speak"
    out.text = str(decision.get("text") or "").strip() or "嗯，我在听。你接着说。"
    out.intent = "casual"
    return out
