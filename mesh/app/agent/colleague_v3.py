"""Colleague v3 — 一张嘴；Ask / Feishu Hands 都是工具。

协议 JSON 不得泄漏。写操作：prepare → 用户确认 → confirm 才真正写入。
"""
from __future__ import annotations

import ast
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .session_state import SessionContextState
from .tool_contract import FEISHU_ALL_TOOLS, FEISHU_READ_TOOLS, FEISHU_WRITE_TOOLS

log = logging.getLogger("uvicorn.error")

_MESH_TOOLS = {
    "ask.published",
    "ask.relations_summary",
    "context.list_issues",
}
_TOOLS = _MESH_TOOLS | set(FEISHU_ALL_TOOLS)

_CONFIRM_RE = re.compile(
    r"^(好的?|可以|要|行|嗯+|确认|创建吧|发吧|发|创建|写吧|就这样|ok|yes|y)\s*[。.!！]*$",
    re.I,
)
_CANCEL_RE = re.compile(
    r"^(不要|别|取消|算了|先不用|no|n)\s*[。.!！]*$",
    re.I,
)

_SYSTEM_DECIDE = """你是 GeekPark 内部 AI 同事 Mesh 的「动作选择」层。
只输出一个严格 JSON（双引号，不要 markdown，不要长文）：

{"action":"speak"}
{"action":"ask","tool":"<tool>","query":"...","resource_type":"doc|message|group|wiki|folder|calendar","args":{}}
{"action":"prepare_write","tool":"feishu.doc.create|feishu.im.send|feishu.calendar.create","args":{...}}
{"action":"confirm_write"}
{"action":"cancel_write"}
{"action":"refuse","text":"一句短拒"}

可读 tool：ask.published | ask.relations_summary | context.list_issues |
  feishu.search | feishu.doc.get | feishu.calendar.list | feishu.discuss.summary
可写 tool（必须先 prepare_write，等用户确认后再 confirm_write）：
  feishu.doc.create | feishu.im.send | feishu.calendar.create

规则：
- 闲聊/情绪/观点/写稿润色（不写入飞书）→ speak
- 已上线周报事实 → ask + ask.published / ask.relations_summary
- 搜飞书文档/Wiki/文件夹 → ask + feishu.search，resource_type=doc|wiki|folder
- 搜聊天/「跟谁聊过」消息侧 → ask + feishu.search resource_type=message，或 feishu.discuss.summary
- 查日程 → ask + feishu.calendar.list（或 search calendar）
- 读某篇文档要点 → ask + feishu.doc.get
- 整理成文但不写入飞书 → speak（直接成文）
- 要创建飞书文档 / 发消息 / 建日程 → prepare_write，args 填齐 title/content 或 receive_id/text 或 title/start/end；不要直接写
- 用户明确确认上一轮准备写入 → confirm_write；明确取消 → cancel_write
- 改权限/读草稿原文 → refuse
- 不要编造飞书内容；Hands 不可用时用 speak 诚实说边界
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
6) 飞书 live 材料与周报事实必须分开说，禁止把飞书讨论说成「周报里记录」。
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
        raw = str(t.get("text") or "")
        lines.append(f"{role}：{_sanitize_user_visible(raw)}")
    pending = getattr(state, "pending_write", None)
    extra = ""
    if isinstance(pending, dict) and pending.get("tool"):
        extra = (
            f"\n（系统：有待确认写操作 tool={pending.get('tool')} "
            f"title={str(pending.get('args') or {}).get('title') or ''}）"
        )
    return "最近对话：\n" + "\n".join(lines) + extra


def _parse_decision(raw: str) -> dict[str, Any]:
    s = (raw or "").strip()
    if not s:
        return {"action": "speak"}
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s).strip()

    try:
        data = json.loads(s)
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

    am = re.search(r"['\"]action['\"]\s*:\s*['\"](\w+)['\"]", s, re.I)
    action = (am.group(1) if am else "speak").lower()
    out: dict[str, Any] = {"action": action}
    tm = re.search(r"['\"]tool['\"]\s*:\s*['\"]([^'\"]+)['\"]", s, re.I)
    if tm:
        out["tool"] = tm.group(1)
    qm = re.search(r"['\"]query['\"]\s*:\s*['\"]([^'\"]*)['\"]", s, re.I)
    if qm:
        out["query"] = qm.group(1)
    if action == "refuse":
        out["text"] = "这个我做不了。"
    return out


def _sanitize_user_visible(text: str) -> str:
    t = (text or "").strip()
    if not t:
        return t
    if not _LEAK_RE.search(t) and "'action'" not in t and '"action"' not in t:
        return t
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
    m = re.search(
        r"['\"]text['\"]\s*:\s*['\"]([\s\S]*?)['\"]\s*\}?\s*$",
        t,
    )
    if m:
        return m.group(1).replace("\\n", "\n").strip()
    log.warning("colleague_v3 stripped leaked protocol from user text")
    return "刚才格式乱了一下。你要我说啥，直接再说一遍。"


def _decide(user_text: str, identity: Any, state: SessionContextState | None) -> tuple[dict[str, Any], dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": ""}
    # 硬确认 / 取消：有 pending 时优先，避免模型漏判
    qn = (user_text or "").strip()
    pending = getattr(state, "pending_write", None) if state else None
    if isinstance(pending, dict) and pending.get("tool"):
        if _CONFIRM_RE.match(qn):
            return {"action": "confirm_write"}, meta
        if _CANCEL_RE.match(qn):
            return {"action": "cancel_write"}, meta

    system = _SYSTEM_DECIDE + "\n\n## 对方\n" + _identity_block(identity)
    hist = _history_block(state)
    user = (hist + "\n\n" if hist else "") + f"用户：{qn}\nJSON："
    try:
        from .. import llm

        raw = llm.call(
            system,
            user,
            max_tokens=320,
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
    *,
    source_tier: str = "published",
) -> tuple[str, dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None}
    tier = (source_tier or "published").strip().lower()
    if tier == "feishu_live":
        material_note = (
            "下面是飞书 live_context 材料，不是已上线周报。"
            "用同事口吻转述；必须标明来自飞书侧；"
            "禁止说成「周报里记录」；空/失败就说没查到，不要编造。"
        )
    else:
        material_note = (
            "下面是已从已上线周报查到的事实材料。"
            "用同事口吻转述：自然、清楚；不要编造材料没有的事实。"
        )
    system = (
        "你是 GeekPark 内部同事 Mesh。"
        + material_note
        + "不要输出 JSON。\n\n"
        + _identity_block(identity)
    )
    hist = _history_block(state)
    user = (
        (hist + "\n\n" if hist else "")
        + f"用户问：{(user_text or '').strip()}\n\n材料：\n{(fact_text or '').strip()}\n\nMesh："
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
    if action in ("prepare_write", "confirm_write"):
        return "feishu_write"
    if action == "ask":
        return {
            "ask.published": "ask_published",
            "ask.relations_summary": "ask_relations",
            "context.list_issues": "list_issues",
            "feishu.search": "feishu_search",
            "feishu.doc.get": "feishu_doc_get",
            "feishu.calendar.list": "feishu_calendar_list",
            "feishu.discuss.summary": "feishu_discuss",
        }.get(tool_id, "ask_published")
    return "casual"


def _default_resource_type(tool: str, decision: dict[str, Any]) -> str:
    rt = str(decision.get("resource_type") or "").strip().lower()
    if rt:
        return rt
    args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    rt2 = str(args.get("resource_type") or "").strip().lower()
    return rt2 or "doc"


def _build_ask_args(tool: str, query: str, decision: dict[str, Any], context: Any = None) -> dict[str, Any]:
    args: dict[str, Any] = {"q": query}
    raw = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    for k, v in raw.items():
        if k not in args and v is not None:
            args[k] = v
    if tool == "feishu.search":
        args["resource_type"] = _default_resource_type(tool, decision)
    if tool == "feishu.discuss.summary" and "person" not in args:
        args.setdefault("person", query[:40])
    chat_id = ""
    if context is not None:
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
    if chat_id:
        args.setdefault("chat_id", chat_id)
        # 当前会话发消息默认目标
        if tool == "feishu.im.send":
            args.setdefault("receive_id", chat_id)
            args.setdefault("receive_id_type", "chat_id")
    return args


def _prepare_write_text(tool: str, args: dict[str, Any]) -> str:
    if tool == "feishu.doc.create":
        title = str(args.get("title") or "未命名文档")
        body = str(args.get("content") or "")[:800]
        return (
            f"我已经整理好了，准备创建到你的飞书空间。\n"
            f"标题：{title}\n"
            f"正文预览：\n{body or '（空）'}\n\n"
            f"要创建吗？回复「确认」或「要」我再写入；「取消」则不作数。"
        )
    if tool == "feishu.im.send":
        rid = str(args.get("receive_id") or "")
        text = str(args.get("text") or "")[:400]
        return (
            f"准备发到 {rid or '（目标未填）'}：\n{text}\n\n"
            f"确认发送吗？回复「确认」；「取消」则不发。"
        )
    if tool == "feishu.calendar.create":
        return (
            f"准备创建日程：{args.get('title') or '未命名'}\n"
            f"时间：{args.get('start')} ~ {args.get('end')}\n"
            f"{args.get('description') or ''}\n\n"
            f"确认创建吗？回复「确认」；「取消」则不作数。"
        )
    return "准备执行写操作。确认吗？"


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
    if action not in (
        "speak",
        "ask",
        "refuse",
        "prepare_write",
        "confirm_write",
        "cancel_write",
    ):
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

    if action == "cancel_write":
        session.pending_write = None
        out.action = "speak"
        out.text = "好，已取消，不会写入飞书。"
        out.intent = "casual"
        out.trace["write_cancelled"] = True
        return out

    if action == "prepare_write":
        tool = str(decision.get("tool") or "").strip()
        if tool not in FEISHU_WRITE_TOOLS:
            out.action = "speak"
            out.text = "这个写入我还不支持。你可以让我先整理成文，你自己贴到飞书。"
            out.intent = "casual"
            return out
        from . import permission as permmod
        from . import feishu_hands

        if not feishu_hands.hands_enabled():
            out.action = "speak"
            out.text = "飞书 Hands 还没开，我可以先帮你把文稿整理好，但不能直接写入飞书。"
            out.intent = "casual"
            return out
        if not feishu_hands.write_enabled():
            # 仍可准备预览；真正写入会失败——明确告诉用户
            pass
        if not permmod.tool_allowed(permission, tool) and feishu_hands.write_enabled():
            out.action = "speak"
            out.text = "你当前没有这项飞书写入权限。"
            out.intent = "casual"
            return out
        args = decision.get("args") if isinstance(decision.get("args"), dict) else {}
        args = dict(args)
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
        if chat_id:
            args.setdefault("chat_id", chat_id)
            if tool == "feishu.im.send":
                args.setdefault("receive_id", chat_id)
                args.setdefault("receive_id_type", "chat_id")
        # 缺正文时用 speak 生成一版 content（文档）
        if tool == "feishu.doc.create" and not str(args.get("content") or "").strip():
            draft, smeta = _speak_plain(
                f"请把下面需求整理成可写入飞书的正文（不要寒暄）：{q}",
                identity,
                session,
            )
            out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
            args["content"] = draft
            args.setdefault("title", (q[:40] or "Mesh 整理").strip())
        if tool == "feishu.im.send" and not str(args.get("text") or "").strip():
            args["text"] = q
        session.pending_write = {"tool": tool, "args": args}
        out.action = "speak"
        out.intent = "feishu_write"
        out.tool_id = tool
        if not feishu_hands.write_enabled():
            out.text = (
                _prepare_write_text(tool, args)
                + "\n\n（当前环境写入开关关闭：即使你确认也不会真正写入，可先当预览。）"
            )
        else:
            out.text = _prepare_write_text(tool, args)
        out.trace["pending_write"] = tool
        return out

    if action == "confirm_write":
        pending = session.pending_write if isinstance(session.pending_write, dict) else None
        if not pending or not pending.get("tool"):
            out.action = "speak"
            out.text = "我这边没有待确认的写入。你要创建文档、发消息还是建日程？"
            out.intent = "casual"
            return out
        tool = str(pending.get("tool") or "")
        args = dict(pending.get("args") or {})
        args["confirmed"] = True
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
        if chat_id:
            args.setdefault("chat_id", chat_id)
            if tool == "feishu.im.send":
                args.setdefault("receive_id", chat_id)
                args.setdefault("receive_id_type", "chat_id")
        from . import permission as permmod
        from . import feishu_hands

        if not feishu_hands.write_enabled():
            session.pending_write = None
            out.action = "speak"
            out.text = "写入开关关着（MESH_FEISHU_HANDS_WRITE），我不能真正写入飞书。预览还在，打开开关后再说「确认」。"
            out.intent = "feishu_write"
            return out
        if not permmod.tool_allowed(permission, tool):
            session.pending_write = None
            out.action = "speak"
            out.text = "你当前没有这项飞书写入权限。"
            out.intent = "casual"
            return out
        result = invoke_tool(tool, con, identity, permission, context, args)
        out.tools_called = [tool]
        out.intent = "feishu_write"
        out.action = "ask"
        fact_text, bindings, evidence = render_tool_result(
            result, "feishu_write", identity.status
        )
        out.claim_bindings = list(bindings or [])
        out.evidence_refs = list(evidence or [])
        out.payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
        session.pending_write = None
        if getattr(result, "ok", False):
            spoken, smeta = _synthesize(
                q,
                fact_text or "写入已完成。",
                identity,
                session,
                source_tier="feishu_live",
            )
            out.synthesize_llm_used = bool(smeta.get("llm_used"))
            out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
            out.text = _sanitize_user_visible(spoken)
        else:
            err = str(getattr(result, "error", "") or "")
            out.text = f"没写进去（{err or '失败'}）。我不会假装已经创建成功。"
        out.trace["write_confirmed"] = tool
        out.trace["source_tier"] = "feishu_live"
        return out

    if action == "ask":
        tool = str(decision.get("tool") or "ask.published").strip()
        if tool not in _TOOLS:
            tool = "ask.published"
        # 误把写工具当 ask：改走 prepare
        if tool in FEISHU_WRITE_TOOLS:
            decision["action"] = "prepare_write"
            decision["tool"] = tool
            return handle(
                con=con,
                user_text=user_text,
                identity=identity,
                permission=permission,
                context=context,
                session=session,
                invoke_tool=invoke_tool,
                render_tool_result=render_tool_result,
            )
        from . import permission as permmod

        if not permmod.tool_allowed(permission, tool):
            out.action = "speak"
            if tool in FEISHU_READ_TOOLS:
                out.text = "飞书 Hands 未开启或不在你权限内；可改问已上线周报，或请管理员打开 MESH_FEISHU_HANDS。"
            else:
                out.text = "这块不在你当前能看的范围里；换个已上线、你有权限的人或事试试。"
            out.intent = "casual"
            return out
        query = str(decision.get("query") or q).strip() or q
        tool_args = _build_ask_args(tool, query, decision, context)
        result = invoke_tool(tool, con, identity, permission, context, tool_args)
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
        is_feishu = tool in FEISHU_ALL_TOOLS
        if is_feishu and not getattr(result, "ok", True):
            err = str(getattr(result, "error", "") or "")
            if err in (
                "hands_disabled",
                "mcp_not_configured",
                "cli_not_configured",
                "write_disabled",
            ):
                fact_text = (
                    "飞书 Hands 这轮还没接通（或未开启）。"
                    "不要编造文档/消息/日程；可请用户稍后再试，或改问已上线周报。"
                )
            elif not fact_text:
                fact_text = "飞书这边这轮没查到 / 超时了。不要编造。"
        source_tier = "feishu_live" if is_feishu else "published"
        spoken, smeta = _synthesize(
            q, fact_text, identity, session, source_tier=source_tier
        )
        out.synthesize_llm_used = bool(smeta.get("llm_used"))
        out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
        out.text = _sanitize_user_visible(spoken)
        out.action = "ask"
        out.trace["synthesize"] = bool(smeta.get("llm_used"))
        out.trace["ask_query"] = query
        out.trace["source_tier"] = source_tier
        return out

    spoken, smeta = _speak_plain(q, identity, session)
    out.action = "speak"
    out.text = _sanitize_user_visible(spoken)
    out.intent = "casual"
    out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
    out.synthesize_llm_used = bool(smeta.get("llm_used"))
    out.trace["speak_plain"] = True
    return out
