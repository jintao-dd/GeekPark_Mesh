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
    r"^(好的?|可以|要|行|嗯+|确认|创建吧|发吧|写吧|就这样|ok|yes|y)\s*[。.!！]*$",
    re.I,
)
_CANCEL_RE = re.compile(
    r"^(不要|别|取消|算了|先不用|no|n)\s*[。.!！]*$",
    re.I,
)


def _clean_user_text(text: str) -> str:
    """去掉飞书 @ 占位（群聊常为 @_user_1）。"""
    from .conversation import normalize_query

    return normalize_query(text or "")


def _looks_like_confirm(text: str) -> bool:
    """仅短句硬确认：有 pending 时防 JSON 炸掉。意图判断交给 LLM。"""
    q = _clean_user_text(text)
    return bool(q and _CONFIRM_RE.match(q))


def _looks_like_cancel(text: str) -> bool:
    q = _clean_user_text(text)
    return bool(q and _CANCEL_RE.match(q))


def _pending_key(context: Any, identity: Any) -> str:
    from . import session_state as sstore

    ch = str(getattr(context, "channel", None) or "").strip()
    chat_id = str(getattr(context, "chat_id", None) or "").strip()
    open_id = str(getattr(identity, "feishu_open_id", None) or "").strip()
    return sstore.pending_key_of(channel=ch, chat_id=chat_id, feishu_open_id=open_id)


def _sync_pending_from_store(session: SessionContextState, context: Any, identity: Any) -> None:
    """跨 worker：从磁盘/粗粒度 store 回填 pending。"""
    from . import session_state as sstore

    if isinstance(session.pending_write, dict) and session.pending_write.get("tool"):
        return
    key = _pending_key(context, identity)
    loaded = sstore.load_pending_write(key)
    if loaded:
        session.pending_write = loaded


def _persist_pending(session: SessionContextState, context: Any, identity: Any) -> None:
    from . import session_state as sstore

    key = _pending_key(context, identity)
    sstore.save_pending_write(key, session.pending_write if isinstance(session.pending_write, dict) else None)


def _clear_pending(session: SessionContextState, context: Any, identity: Any) -> None:
    from . import session_state as sstore

    session.pending_write = None
    sstore.clear_pending_write(_pending_key(context, identity))


def _merge_confirm_args(tool: str, args: dict[str, Any], user_text: str) -> dict[str, Any]:
    """确认句若夹带新内容，并入 args（由 LLM 决策确认后执行时使用）。"""
    out = dict(args)
    q = _clean_user_text(user_text)
    if tool == "feishu.doc.create" and len(q) > 4:
        cm = re.search(r"内容(?:主要)?(?:是|为)?[：:]?\s*(.+)$", q, re.I | re.S)
        if cm and cm.group(1).strip():
            out["content"] = cm.group(1).strip()
        elif q.startswith("确认") and len(q) > 8:
            # 「确认，再加点自我介绍」之类：去掉开头确认词后若仍有实质，当作补充说明并入
            rest = re.sub(r"^确认[，,。\s]*", "", q).strip()
            if rest and not re.match(r"^(创建|发送|写入|执行|吧|了)+$", rest):
                if len(str(out.get("content") or "")) < 20:
                    out["content"] = (str(out.get("content") or "") + "\n" + rest).strip()
        tm = re.search(r"标题\s*[：:]\s*([^\s，,。]{1,40})", q)
        if tm:
            out["title"] = tm.group(1).strip()
    if tool == "feishu.im.send" and len(q) > 4:
        m = re.match(r"^确认[，,\s]*(?:发送|发)?[：:]?\s*(.*)$", q, re.I | re.S)
        extra = (m.group(1) if m else "").strip()
        if extra and len(extra) > 1:
            out["text"] = extra
    return out

_SYSTEM_DECIDE = """你是 GeekPark 内部 AI 同事 Mesh 的「调度」层。
只输出一个极短严格 JSON（双引号，无 markdown，无长文）。必须含 situation + action：

{"situation":"chat","action":"speak"}
{"situation":"need_published","action":"ask","tool":"ask.published","query":"..."}
{"situation":"need_feishu_read","action":"ask","tool":"feishu.search","query":"...","resource_type":"doc|message|group|wiki|folder|calendar|member|user|directory","args":{}}
{"situation":"want_feishu_write","action":"prepare_write","tool":"feishu.doc.create|feishu.im.send|feishu.calendar.create","args":{"title":"短","content":""}}
{"situation":"confirm_pending","action":"confirm_write"}
{"situation":"cancel_pending","action":"cancel_write"}
{"situation":"refuse","action":"refuse","text":"一句短拒"}

数据隔离（硬）：
- published 周报事实 ↔ feishu_live 飞书 live ↔ speak 草稿，三桶禁止混成一条事实
- 写飞书只能 prepare_write→confirm_write，禁止用 speak 假装「正在创建/已创建」

可读 tool：ask.published | ask.relations_summary | context.list_issues |
  feishu.search | feishu.doc.get | feishu.calendar.list | feishu.discuss.summary
可写 tool：feishu.doc.create | feishu.im.send | feishu.calendar.create

飞书读路由提示（结构化，不要把用户整句当检索词）：
- 列群 → tool=feishu.search, resource_type=group, query=""（或 args.keyword=群名）
- 列本群成员 → feishu.search + member, query=""（需当前 chat_id；私聊无 chat 时先问要哪个群）
- 找同事/通讯录/公司里谁叫X/组织架构里的人 → feishu.search + directory，args.keyword=姓名或工号短词；空 keyword 可列可见范围人员/部门
- @某人 / 他是谁（有 mentions.open_id）→ feishu.search + user，args.open_ids=[...]
- 日程 → tool=feishu.calendar.list, query 可空；不要塞整句
- 会话消息 → feishu.search + message；关键词放 args.keyword / 短 query
- 文档/Wiki → feishu.search + doc|wiki，短检索词
- feishu.search 必须带 resource_type
- 通讯录可见范围 = 飞书应用通讯录权限；有结果就报姓名+open_id，没结果如实说查不到

规则（看「系统工作记忆」+ 对话，不要枚举用户句式）：
- 有待确认写操作 + 用户同意 → confirm_write
- 有待确认 + 用户取消 → cancel_write
- 有未完成 active_goal（family=feishu_write）且用户在催/继续要写 → prepare_write 或 confirm_write，禁止闲聊搪塞
- 有 last_block=scope_denied：可诚实说权限；若用户说已开权限 → 再 prepare_write，不要只道歉
- 闲聊/成文不落飞书 → speak
- 周报事实 → ask.published；飞书搜读 → feishu.*（feishu_live）
- prepare_write 的 args.content 必须是 ""；title≤20 字；JSON 通常 <150 字符
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
7) **禁止谎称**：本轮若没有真正调用飞书写入，严禁说「已创建 / 正在创建 / 写入成功 / 已经写进飞书」。没有工具结果就老实说还没写入，或给出可粘贴正文让用户自己建。
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
        mem = _working_memory_block(state)
        return mem
    lines = []
    for t in (state.recent_turns or [])[-8:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        raw = str(t.get("text") or "")
        lines.append(f"{role}：{_sanitize_user_visible(raw)}")
    mem = _working_memory_block(state)
    body = "最近对话：\n" + "\n".join(lines)
    return body + (("\n" + mem) if mem else "")


def _working_memory_block(state: SessionContextState | None) -> str:
    if not state:
        return ""
    bits: list[str] = []
    pending = getattr(state, "pending_write", None)
    if isinstance(pending, dict) and pending.get("tool"):
        bits.append(
            f"- 待确认写操作 tool={pending.get('tool')} "
            f"title={str((pending.get('args') or {}).get('title') or '')}"
        )
    goal = getattr(state, "active_goal", None)
    if isinstance(goal, dict) and goal.get("summary"):
        bits.append(
            f"- 未完成目标 family={goal.get('family') or ''} "
            f"tool={goal.get('tool') or ''} summary={str(goal.get('summary') or '')[:80]}"
        )
    block = getattr(state, "last_block", None)
    if isinstance(block, dict) and block.get("code"):
        bits.append(
            f"- 上一刀失败 code={block.get('code')} tool={block.get('tool') or ''} "
            f"msg={str(block.get('message') or '')[:100]}"
        )
    if not bits:
        return ""
    return "系统工作记忆（短期，非长期记忆）：\n" + "\n".join(bits)


def _set_active_goal(
    session: SessionContextState,
    *,
    summary: str,
    family: str,
    tool: str = "",
    utterance: str = "",
) -> None:
    import time

    session.active_goal = {
        "summary": (summary or "")[:160],
        "family": family,
        "tool": tool or "",
        "utterance": (utterance or "")[:200],
        "updated_at": time.time(),
    }


def _clear_active_goal(session: SessionContextState) -> None:
    session.active_goal = None


def _set_last_block(
    session: SessionContextState,
    *,
    code: str,
    tool: str = "",
    message: str = "",
) -> None:
    import time

    session.last_block = {
        "code": code,
        "tool": tool or "",
        "message": (message or "")[:200],
        "at": time.time(),
    }


def _clear_last_block(session: SessionContextState) -> None:
    session.last_block = None


def _classify_block(err: str, *, tool: str = "") -> dict[str, str]:
    s = (err or "").lower()
    raw = err or ""
    if (
        "99991672" in raw
        or "scope" in s
        or "permission" in s
        or "access denied" in s
        or "docx:document" in s
        or "权限" in raw
        or "scope_denied" in s
    ):
        code = "scope_denied"
    elif any(
        x in s
        for x in (
            "auth_token",
            "token_missing",
            "no access token",
            "user_token_required",
            "user_identity_required",
        )
    ):
        code = "feishu_api"
    elif any(x in s for x in ("hands_disabled", "write_disabled", "mcp_not_configured", "cli_not_configured")):
        code = "hands_off"
    elif any(x in s for x in ("empty", "not_found", "no_result")):
        code = "empty"
    elif s.startswith("feishu_api_") or "feishu_api_" in s:
        code = "feishu_api"
    else:
        code = "other"
    return {"code": code, "tool": tool, "message": raw[:200]}


_FAKE_HANDS_CLAIM_RE = re.compile(
    r"(已创建|正在创建|创建成功|写入成功|已经写进飞书|已发到飞书|日程已建好|调用\s*feishu)",
    re.I,
)


def _strip_fake_hands_claims(text: str) -> str:
    t = (text or "").strip()
    if not t or not _FAKE_HANDS_CLAIM_RE.search(t):
        return t
    log.warning("colleague_v3 blocked fake Hands claim in speak")
    return (
        "这轮我还没有真正写入飞书，不能假装已经创建成功。"
        "要落飞书文档的话直接说「创建文档…」；我先准备预览，你确认后再写。"
    )


def _block_user_text(code: str, err: str) -> str:
    if code == "scope_denied":
        return (
            f"没写进去：应用缺权限（{err[:120]}）。"
            "请在开放平台勾选并**发布版本**后说「再试」；我不会假装已创建。"
        )
    if code == "hands_off":
        return "飞书 Hands / 写入开关没开，这轮写不了。打开后再让我确认写入。"
    if code == "empty":
        return "飞书侧这轮没返回可用结果，我没编造。"
    return f"没写进去（{err or '失败'}）。我不会假装已经创建成功。"


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


def _normalize_decide(decision: dict[str, Any]) -> dict[str, Any]:
    """丢掉 decide 里过长的 content（模型爱塞正文导致 JSON 截断）；意图仍由 LLM 决定。"""
    out = dict(decision or {})
    args = out.get("args")
    if isinstance(args, dict) and len(str(args.get("content") or "")) > 120:
        args = dict(args)
        args["content"] = ""
        out["args"] = args
    return out


def _mentions_block(context: Any = None, state: SessionContextState | None = None) -> str:
    mentions: list[Any] = []
    if context is not None:
        mentions = list(getattr(context, "mentions", None) or [])
    if not mentions and state is not None:
        mentions = list(getattr(state, "last_mentions", None) or [])
    rows = []
    for m in mentions:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        oid = str(m.get("open_id") or "").strip()
        if not name and not oid:
            continue
        rows.append(f"- @{name or '同事'} open_id={oid or '未知'}")
    if not rows:
        return ""
    return "本轮/@最近提及的人（结构化，可查 user）：\n" + "\n".join(rows[:12])


def _decide(
    user_text: str,
    identity: Any,
    state: SessionContextState | None,
    context: Any = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": ""}
    qn = _clean_user_text(user_text)
    pending = getattr(state, "pending_write", None) if state else None
    # 仅短句硬确认/取消：防模型 JSON 炸掉；复合意图一律交给上下文 LLM（禁止枚举用户句式）
    if isinstance(pending, dict) and pending.get("tool"):
        if _looks_like_confirm(qn):
            meta["hard_confirm"] = True
            return {"action": "confirm_write"}, meta
        if _looks_like_cancel(qn):
            meta["hard_cancel"] = True
            return {"action": "cancel_write"}, meta

    system = _SYSTEM_DECIDE + "\n\n## 对方\n" + _identity_block(identity)
    hist = _history_block(state)
    ment = _mentions_block(context, state)
    user = (hist + "\n\n" if hist else "")
    if ment:
        user += ment + "\n\n"
    user += f"用户：{qn}\nJSON："
    decision: dict[str, Any] = {"action": "speak"}
    try:
        from .. import llm

        raw = llm.call(
            system,
            user,
            max_tokens=256,
            json_mode=True,
            task="answer",
        )
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        if isinstance(raw, dict):
            decision = raw
        else:
            decision = _parse_decision(str(raw or ""))
    except Exception as e:
        log.warning("colleague_v3 decide failed: %s", e)
        meta["error"] = str(e)[:120]
        # 再请 LLM 判一次（仍是上下文判断，不是关键词枚举）；强制极短 JSON
        try:
            from .. import llm

            raw2 = llm.call(
                system,
                user
                + "\n\n上次 JSON 非法。按对话上下文重新选 action；"
                "只输出一个极短 JSON；prepare_write 时 args.content 必须是 \"\"。",
                max_tokens=256,
                json_mode=False,
                task="answer",
            )
            meta["llm_used"] = True
            meta["salvage"] = True
            meta["model"] = llm.model_for_task("answer")
            decision = _parse_decision(str(raw2 or ""))
        except Exception as e2:
            log.warning("colleague_v3 decide salvage failed: %s", e2)
            meta["error"] = (meta.get("error") or "") + "|" + str(e2)[:80]
            decision = {"action": "speak"}

    return _normalize_decide(decision if isinstance(decision, dict) else {"action": "speak"}), meta


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
    fact = (fact_text or "").strip()
    if tier == "feishu_live":
        material_note = (
            "下面是飞书 live_context 材料，不是已上线周报。"
            "用同事口吻转述；必须标明来自飞书侧；"
            "禁止说成「周报里记录」；"
            "材料里已有条目时必须如实转述，禁止说成没查到/权限没打通；"
            "仅当材料明确写空/失败时才说没查到；不要自己猜测权限问题。"
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
        + f"用户问：{(user_text or '').strip()}\n\n材料：\n{fact}\n\nMesh："
    )
    try:
        from .. import llm

        out = llm.call(system, user, max_tokens=2500, json_mode=False, task="answer")
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        text = _sanitize_user_visible(str(out or "").strip())
        # 有材料却被模型说成权限空结果：回落材料正文
        if fact and re.search(r"^- ", fact, re.M) and re.search(
            r"(没查到|权限没|没打通|可能是权限)", text or ""
        ):
            log.warning("colleague_v3 synthesize contradicted non-empty feishu materials")
            return fact, meta
        return (text or fact), meta
    except Exception as e:
        log.warning("colleague_v3 synthesize failed: %s", e)
        return fact, meta


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
    if rt2:
        return rt2
    return ""


def _infer_search_resource_type(query: str) -> str:
    """Decide 漏了 resource_type 时，按本轮用户话即时判断（非关键词表）。"""
    q = (query or "").strip()
    if not q:
        return "doc"
    allowed = ("doc", "message", "group", "wiki", "folder", "calendar", "member", "user", "directory")
    system = (
        "你给飞书搜索选 resource_type。"
        "只输出其中一个单词：doc / message / group / wiki / folder / calendar / member / user / directory。"
        "列群/群聊→group；列群成员/谁在群里→member；@某人/已知open_id查此人→user；"
        "找同事/通讯录/公司里谁/按姓名搜人/组织架构→directory；"
        "日程/开会/周会→calendar；聊天消息→message；知识库→wiki；文件夹→folder；云文档→doc。"
        "不要解释。"
    )
    try:
        from .. import llm

        out = str(llm.call(system, f"用户：{q}\nresource_type：", max_tokens=8, json_mode=False, task="answer") or "").strip().lower()
        word = re.split(r"[\s,，。；;]", out)[0].strip()
        if word in allowed:
            return word
    except Exception as e:
        log.warning("infer resource_type failed: %s", e)
    return "doc"


def _build_ask_args(
    tool: str,
    query: str,
    decision: dict[str, Any],
    context: Any = None,
    session: SessionContextState | None = None,
) -> dict[str, Any]:
    """组装工具参数：用户原话只作 Decide 输入；工具侧只用结构化字段。"""
    args: dict[str, Any] = {}
    raw = decision.get("args") if isinstance(decision.get("args"), dict) else {}
    for k, v in raw.items():
        if v is not None:
            args[k] = v
    # Decide 可给 keyword；不要默认把整句用户话塞进 q 当过滤器
    keyword = str(args.get("keyword") or args.get("name") or "").strip()
    explicit_q = str(args.get("q") or args.get("query") or "").strip()
    if tool == "feishu.search":
        rt = _default_resource_type(tool, decision)
        if not rt:
            rt = _infer_search_resource_type(query)
            log.info("colleague_v3 inferred resource_type=%s q=%r", rt, (query or "")[:60])
        args["resource_type"] = rt
        if keyword:
            args["q"] = keyword
            args["keyword"] = keyword
        elif explicit_q:
            args["q"] = explicit_q
        else:
            # 列表类：空 q；搜索类：留给 Decide 的 query 字段（短检索词），不是用户整句
            decide_q = str(decision.get("query") or "").strip()
            args["q"] = decide_q if decide_q and decide_q != (query or "").strip() else ""
            # 若 Decide 把整句放进 query，搜索词也清空，由 resource_type 决定 list/search
            if args["q"] == (query or "").strip() and rt in ("group", "calendar", "member", "directory"):
                args["q"] = ""
        # @人 / 查人：从 args 或上下文 mentions 注入 open_ids
        if rt == "user":
            oids: list[str] = []
            for x in args.get("open_ids") or []:
                s = str(x or "").strip()
                if s and s not in oids:
                    oids.append(s)
            one = str(args.get("open_id") or args.get("user_id") or "").strip()
            if one and one not in oids:
                oids.insert(0, one)
            if not oids:
                mention_sources = []
                if context is not None:
                    mention_sources.append(getattr(context, "mentions", None) or [])
                if session is not None:
                    mention_sources.append(getattr(session, "last_mentions", None) or [])
                for src in mention_sources:
                    for m in src or []:
                        if not isinstance(m, dict):
                            continue
                        s = str(m.get("open_id") or "").strip()
                        if s and s not in oids:
                            oids.append(s)
            if oids:
                args["open_ids"] = oids
                args.setdefault("open_id", oids[0])
        if rt in ("directory", "org"):
            args["resource_type"] = "directory"
            # 姓名/工号短词；整句清空
            if args.get("q") == (query or "").strip():
                args["q"] = keyword or ""
                if keyword:
                    args["keyword"] = keyword
    elif tool == "feishu.calendar.list":
        args["q"] = keyword
        if keyword:
            args["keyword"] = keyword
        args.setdefault("days", int(args.get("days") or 7))
    else:
        args.setdefault("q", explicit_q or str(decision.get("query") or query or "").strip())
    if tool == "feishu.discuss.summary" and "person" not in args:
        args.setdefault("person", (keyword or query)[:40])
    chat_id = ""
    if context is not None:
        chat_id = str(getattr(context, "chat_id", None) or "").strip()
    if chat_id:
        args.setdefault("chat_id", chat_id)
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
    q = _clean_user_text(user_text)
    _sync_pending_from_store(session, context, identity)
    # 本轮 @ 进会话，便于下句「他是谁」
    cur_mentions = list(getattr(context, "mentions", None) or [])
    if cur_mentions:
        session.last_mentions = [
            {
                "open_id": str(m.get("open_id") or "").strip(),
                "name": str(m.get("name") or "").strip(),
            }
            for m in cur_mentions
            if isinstance(m, dict)
            and (str(m.get("open_id") or "").strip() or str(m.get("name") or "").strip())
        ][:12]
    decision, dmeta = _decide(q, identity, session, context)
    action = str(decision.get("action") or "speak").strip().lower()
    log.info(
        "colleague_v3 decide action=%s tool=%s pending=%s hard=%s q=%r",
        action,
        str(decision.get("tool") or "")[:40],
        bool(isinstance(session.pending_write, dict) and (session.pending_write or {}).get("tool")),
        bool(dmeta.get("hard_confirm") or dmeta.get("hard_cancel")),
        (q or "")[:80],
    )
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
            "hard_confirm": bool(dmeta.get("hard_confirm")),
            "hard_cancel": bool(dmeta.get("hard_cancel")),
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
        _clear_pending(session, context, identity)
        _clear_active_goal(session)
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
        # 缺正文：用用户原话当写作任务生成（「创建新文档详细介绍你自己」→ 生成自我介绍）
        if tool == "feishu.doc.create" and not str(args.get("content") or "").strip():
            want_empty = bool(
                re.search(r"空的?文档|空文档", q)
                and not re.search(r"介绍|关于|写|内容|说明", q)
            )
            if want_empty:
                args["content"] = ""
                args.setdefault("title", "未命名文档")
            else:
                draft, smeta = _speak_plain(
                    "用户要创建飞书文档。请按用户原话直接写出可粘贴进文档的完整正文"
                    "（不要寒暄、不要问要不要创建、不要输出 JSON）。\n"
                    f"用户原话：{q}",
                    identity,
                    session,
                )
                out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
                args["content"] = draft
                if not str(args.get("title") or "").strip():
                    args["title"] = (q[:32] or "Mesh 整理").strip()
        if tool == "feishu.im.send" and not str(args.get("text") or "").strip():
            args["text"] = q
        session.pending_write = {"tool": tool, "args": args}
        _persist_pending(session, context, identity)
        _set_active_goal(
            session,
            summary=f"写入 {tool}" + (f"：{args.get('title')}" if args.get("title") else ""),
            family="feishu_write",
            tool=tool,
            utterance=q,
        )
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
        out.trace["active_goal"] = session.active_goal
        return out

    if action == "confirm_write":
        pending = session.pending_write if isinstance(session.pending_write, dict) else None
        if not pending or not pending.get("tool"):
            out.action = "speak"
            out.text = "我这边没有待确认的写入。你要创建文档、发消息还是建日程？"
            out.intent = "casual"
            return out
        tool = str(pending.get("tool") or "")
        args = _merge_confirm_args(tool, dict(pending.get("args") or {}), q)
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
            _set_last_block(session, code="hands_off", tool=tool, message="write_disabled")
            out.action = "speak"
            out.text = "写入开关关着（MESH_FEISHU_HANDS_WRITE），我不能真正写入飞书。预览还在，打开开关后再说「确认」。"
            out.intent = "feishu_write"
            return out
        if not permmod.tool_allowed(permission, tool):
            _clear_pending(session, context, identity)
            _set_last_block(session, code="scope_denied", tool=tool, message="tool_acl")
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
        _clear_pending(session, context, identity)
        if getattr(result, "ok", False):
            _clear_active_goal(session)
            _clear_last_block(session)
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
            meta = out.payload.get("meta") if isinstance(out.payload, dict) else None
            if isinstance(meta, dict):
                url = str(meta.get("url") or "").strip()
                if url and url not in out.text:
                    out.text = out.text.rstrip() + f"\n\n文档地址：{url}"
                if meta.get("tenant_share") is False:
                    out.text = (
                        out.text.rstrip()
                        + "\n\n（提醒：公司内链接权限这次没设上，同事可能打不开；可手动开「组织内获得链接可阅读」。）"
                    )
                if meta.get("member_grant") is False and str(meta.get("doc_token") or ""):
                    out.text = (
                        out.text.rstrip()
                        + "\n\n（提醒：这次没能把编辑权限授给你本人，文档仍在机器人名下；用上面链接打开，必要时让我再改权限。）"
                    )
        else:
            err = str(getattr(result, "error", "") or "")
            classified = _classify_block(err, tool=tool)
            _set_last_block(
                session,
                code=classified["code"],
                tool=tool,
                message=classified["message"],
            )
            out.text = _block_user_text(classified["code"], err)
        out.trace["write_confirmed"] = tool
        out.trace["source_tier"] = "feishu_live"
        out.trace["last_block"] = session.last_block
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
        tool_args = _build_ask_args(tool, query, decision, context, session)
        result = invoke_tool(tool, con, identity, permission, context, tool_args)
        out.tools_called = [tool]
        intent = _intent_for("ask", tool)
        out.intent = intent
        fact_text, bindings, evidence = render_tool_result(result, intent, identity.status)
        out.claim_bindings = list(bindings or [])
        out.evidence_refs = list(evidence or [])
        out.payload = result.payload if isinstance(getattr(result, "payload", None), dict) else {}
        log.info(
            "colleague_v3 ask tool=%s rt=%s ok=%s n=%s q=%r",
            tool,
            str(tool_args.get("resource_type") or ""),
            bool(getattr(result, "ok", False)),
            int((out.payload or {}).get("n_hits") or 0),
            (query or "")[:60],
        )
        if getattr(result, "denied", False):
            out.refused = True
            out.deny_reason = str(getattr(result, "error", "") or "ask_denied")
            out.text = _sanitize_user_visible(fact_text)
            out.action = "ask"
            return out
        is_feishu = tool in FEISHU_ALL_TOOLS
        if is_feishu and not getattr(result, "ok", True):
            err = str(getattr(result, "error", "") or "")
            classified = _classify_block(err, tool=tool)
            _set_last_block(
                session,
                code=classified["code"],
                tool=tool,
                message=classified["message"],
            )
            out.trace["last_block"] = session.last_block
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
    out.text = _strip_fake_hands_claims(_sanitize_user_visible(spoken))
    out.intent = "casual"
    out.llm_used = bool(out.llm_used or smeta.get("llm_used"))
    out.synthesize_llm_used = bool(smeta.get("llm_used"))
    out.trace["speak_plain"] = True
    out.trace["source_tier"] = "model"
    return out
