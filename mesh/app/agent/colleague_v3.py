"""Colleague v3 — 单一主体：一张嘴听+说；Ask 只是工具。

协议（action JSON）绝不能泄漏到用户可见文本。
Decide 只出短 JSON；speak 正文另用明文生成。
"""
from __future__ import annotations

import ast
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

_SYSTEM_DECIDE = """你是 GeekPark 内部 AI 同事 Mesh 的「动作选择」层。
只输出一个严格 JSON（双引号，不要 markdown，不要长文）：

{"action":"speak"}
{"action":"ask","tool":"ask.published|ask.relations_summary|context.list_issues","query":"..."}
{"action":"refuse","text":"一句短拒"}

规则：
- 闲聊/吐槽/情绪/观点/写稿润色/改写/自我反馈/用户让你直接做事 → speak（正文稍后生成，JSON 里不要写正文）
- 明确要查已上线周报事实（某人跟谁聊过、关系、期次）→ ask，query 写成完整问句
- 改权限/发布/读草稿原文 → refuse
- 写文章、直接写、不要问 → 必须 speak，不要 ask 周报
- 对象不清且确实是查周报 → 用 speak（稍后用嘴问一句），不要瞎 ask
"""

_SYSTEM_SPEAK = """你是 GeekPark（极客公园）内部的 AI 同事「Mesh」，在飞书里和员工说话。

你是一个人，不是路由、不是客服、不是检索框。
自然、有态度；可以有情绪和观点；禁止客服腔、「很抱歉给您带来不便」、每轮追问还需要什么、强行 emoji、自我介绍（除非被问是谁）。

长短：
- 按内容需要写，不要刻意压成一两句「短答客服」。
- 用户指定字数/篇幅（如 300–500 字、写一篇）——必须写够，宁可稍长，不要缩成摘要交差。
- 闲聊可以多说几句有来有回；解释/观点可以展开理由；写稿/润色给完整可用成品。
- 只有「谢谢/好的/在吗」这类极短回合，才简短回应。

硬规则：
1) 用户说「直接写 / 不要问了 / 写啊」——本轮必须交付成品，禁止只承诺「下次」、禁止再追问风格受众。
2) 写稿/润色：直接给一版可用的；不确定风格就选自然有态度的内部同事写法，写完可补一句「要改风格再说」。
3) 不要编造「某人在周报里怎样」的公司事实；若用户在问周报事实，自然说需要人名/公司或请他们直接问。
4) 观点用「我觉得」可以，并标明是看法。
5) 只输出对用户说的话，不要输出 JSON、不要输出 action/tool 字段。
"""


@dataclass
class ColleagueV3Result:
    action: str = "speak"
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


_LEAK_RE = re.compile(
    r"""^\s*[\{\[]\s*['"]action['"]\s*:""",
    re.I,
)


def _identity_block(identity: Any) -> str:
    if identity is None:
        return "对方身份：未知（尚未绑定）。不确定时不要假装认识。"
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
    lines.append("可自然使用身份，但不要每句点名汇报。")
    return "\n".join(lines)


def _history_block(state: SessionContextState | None) -> str:
    if not state or not state.recent_turns:
        return ""
    lines = []
    for t in (state.recent_turns or [])[-8:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        # 历史里若曾泄漏 JSON，只留可读片段，避免模型学会泄漏
        raw = str(t.get("text") or "")
        lines.append(f"{role}：{_sanitize_user_visible(raw)}")
    return "最近对话：\n" + "\n".join(lines)


def _parse_decision(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if not s:
        return {"action": "speak"}
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()

    for candidate in (s,):
        try:
            data = json.loads(candidate)
            if isinstance(data, dict) and data.get("action"):
                return data
        except Exception:
            pass

    m = re.search(r"\{[\s\S]*\}", s)
    blob = m.group(0) if m else s
    try:
        data = json.loads(blob)
        if isinstance(data, dict) and data.get("action"):
            return data
    except Exception:
        pass
    try:
        data = ast.literal_eval(blob)
        if isinstance(data, dict) and data.get("action"):
            return {str(k): v for k, v in data.items()}
    except Exception:
        pass

    # 从脏文本抽 action
    am = re.search(r"['\"]action['\"]\s*:\s*['\"](\w+)['\"]", s, re.I)
    action = (am.group(1) if am else "speak").lower()
    out: dict[str, Any] = {"action": action}
    tm = re.search(r"['\"]tool['\"]\s*:\s*['\"]([^'\"]+)['\"]", s, re.I)
    if tm:
        out["tool"] = tm.group(1)
    qm = re.search(r"['\"]query['\"]\s*:\s*['\"]([^'\"]*)['\"]", s, re.I)
    if qm:
        out["query"] = qm.group(1)
    # 绝不把整段协议当 text
    if action == "refuse":
        out["text"] = "这个我做不了。"
    return out


def _sanitize_user_visible(text: str) -> str:
    """防止协议 JSON 漏到飞书。"""
    t = (text or "").strip()
    if not t:
        return t
    if not _LEAK_RE.search(t) and "'action'" not in t and '"action"' not in t:
        return t
    # 尝试抽出 text 字段
    for parser in (json.loads, ast.literal_eval):
        try:
            m = re.search(r"\{[\s\S]*\}", t)
            data = parser(m.group(0) if m else t)
            if isinstance(data, dict):
                inner = str(data.get("text") or "").strip()
                if inner and "'action'" not in inner and '"action"' not in inner[:20]:
                    return inner
        except Exception:
            pass
    # 宽松抽 text
    m = re.search(
        r"['\"]text['\"]\s*:\s*['\"]([\s\S]*?)['\"]\s*\}?\s*$",
        t,
    )
    if m:
        return m.group(1).replace("\\n", "\n").strip()
    # 抽失败：给短回落，绝不回整段 JSON
    log.warning("colleague_v3 stripped leaked protocol from user text")
    return "刚才格式乱了一下。你要我说啥，直接再说一遍。"


def _decide(user_text: str, identity: Any, state: SessionContextState | None) -> tuple[dict[str, Any], dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": ""}
    system = _SYSTEM_DECIDE + "\n\n## 对方\n" + _identity_block(identity)
    hist = _history_block(state)
    user = (hist + "\n\n" if hist else "") + f"用户：{(user_text or '').strip()}\nJSON："
    try:
        from .. import llm

        raw = llm.call(
            system,
            user,
            max_tokens=220,
            json_mode=True,
            task="answer",
        )
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        return _parse_decision(str(raw or "")), meta
    except Exception as e:
        log.warning("colleague_v3 decide failed: %s", e)
        meta["error"] = str(e)[:120]
        return {"action": "speak"}, meta


def _speak_plain(
    user_text: str,
    identity: Any,
    state: SessionContextState | None,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None}
    system = _SYSTEM_SPEAK + "\n\n## 对方\n" + _identity_block(identity)
    hist = _history_block(state)
    user = (hist + "\n\n" if hist else "") + f"用户：{(user_text or '').strip()}\nMesh："
    try:
        from .. import llm

        out = llm.call(
            system,
            user,
            max_tokens=4000,
            json_mode=False,
            task="answer",
        )
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        text = _sanitize_user_visible(str(out or "").strip())
        return (text or "嗯，我在听。你接着说。"), meta
    except Exception as e:
        log.warning("colleague_v3 speak failed: %s", e)
        return "刚才卡了一下。你再说一遍。", meta


def _synthesize(
    user_text: str,
    fact_text: str,
    identity: Any,
    state: SessionContextState | None,
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None}
    system = (
        "你是 GeekPark 内部同事 Mesh。下面是已从已上线周报查到的事实材料。"
        "用同事口吻转述：自然、清楚；不要编造材料没有的事实；不要客服腔；不要输出 JSON。\n\n"
        + _identity_block(identity)
    )
    hist = _history_block(state)
    user = (
        (hist + "\n\n" if hist else "")
        + f"用户问：{(user_text or '').strip()}\n\n事实材料：\n{(fact_text or '').strip()}\n\nMesh："
    )
    try:
        from .. import llm

        out = llm.call(system, user, max_tokens=2500, json_mode=False, task="answer")
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        text = _sanitize_user_visible(str(out or "").strip())
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
    q = (user_text or "").strip()
    decision, dmeta = _decide(q, identity, session)
    action = str(decision.get("action") or "speak").strip().lower()
    if action not in ("speak", "ask", "refuse"):
        action = "speak"

    out = ColleagueV3Result(
        llm_used=bool(dmeta.get("llm_used")),
        model=dmeta.get("model"),
        trace={
            "colleague_v3": True,
            "decide_error": dmeta.get("error") or "",
            "decision": {
                "action": action,
                "tool": str(decision.get("tool") or ""),
                "query": str(decision.get("query") or "")[:200],
            },
        },
    )

    if action == "refuse":
        out.action = "refuse"
        out.text = _sanitize_user_visible(
            str(decision.get("text") or "这个我做不了。").strip()
        )
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
            out.text = _sanitize_user_visible(fact_text)
            out.action = "ask"
            return out
        spoken, smeta = _synthesize(q, fact_text, identity, session)
        out.synthesize_llm_used = bool(smeta.get("llm_used"))
        out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
        out.text = _sanitize_user_visible(spoken)
        out.action = "ask"
        out.trace["synthesize"] = bool(smeta.get("llm_used"))
        out.trace["ask_query"] = query
        return out

    # speak：明文成文（协议与正文分离）
    spoken, smeta = _speak_plain(q, identity, session)
    out.action = "speak"
    out.text = _sanitize_user_visible(spoken)
    out.intent = "casual"
    out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
    out.synthesize_llm_used = bool(smeta.get("llm_used"))
    out.trace["speak_plain"] = True
    return out
