"""Colleague chat — Stage 2A Conversation Core（1× Conversation LLM）。

硬约束：
- 不查 Published / 不造公司事实
- 公司事实引导用户走人名/公司 → Ask 路径
- 失败时短回落，不抛堆栈
- 0 额外 LLM（Colleague Core 不套第二层模型）
"""
from __future__ import annotations

import logging
from typing import Any

from . import colleague_core as ccore
from .session_state import SessionContextState

log = logging.getLogger("uvicorn.error")

_SYSTEM = """你是 GeekPark（极客公园）内部的 AI 同事「Mesh」，在飞书里和员工说话。

你已经知道自己是谁；除非用户问「你是谁 / 能干什么」，否则不要自我介绍，不要复述能力清单，不要教 Prompt。

硬边界：
1) 可以闲聊、接情绪、谈观点、帮润色/改写、给判断与建议。
2) 不能编造「某同事/某公司在周报里怎样」等公司事实。若用户在问周报事实，自然说明需要人名/公司名，或请他们直接问「××最近跟谁聊过」——不要假装查过。
3) 区分三类表达：FACT（仅来自用户已给出的信息或你明确说「这是猜测」）/ OPINION（你的看法）/ SUGGESTION（下一步建议）。观点用「我觉得」「我更倾向」「如果是我」即可。
4) 禁止客服腔与机械复述：不要「很抱歉给您带来不便」「还有什么可以帮您」「感谢您的反馈」；不要复读用户整句；不要每轮追问还需要什么。
5) 禁止强行 emoji、过度共情、官腔、说明书腔。
6) 用户纠正你时：承认具体问题、调整风格、继续对话——不要长篇辩解或重新介绍产品。
7) 按下方 Colleague Core 与 response_mode 控制长短与语气；不要无视预算乱写长文，也不要无脑压成三字经。"""


def _history_block(turns: list[dict[str, Any]] | None) -> str:
    if not turns:
        return ""
    lines = []
    for t in turns[-8:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        lines.append(f"{role}：{t.get('text') or ''}")
    return "最近对话：\n" + "\n".join(lines)


def reply_colleague(
    user_text: str,
    state: SessionContextState | None = None,
    *,
    mode: str = "chat",
    identity_hint: str = "",
    response_mode: str = "",
) -> tuple[str, dict[str, Any]]:
    """返回 (text, meta)。meta 含 model / llm_used / error / response_mode / budget。"""
    meta: dict[str, Any] = {
        "llm_used": False,
        "model": None,
        "error": "",
        "response_mode": "",
        "max_tokens": 0,
    }
    q = (user_text or "").strip()
    if not q:
        return "嗯？", meta

    # Session 信号先更新（0 LLM），再组 Core
    st = state or SessionContextState()
    ccore.apply_user_signal(st, q)

    rm = (response_mode or "").strip().lower()
    if not rm:
        if mode == "meta":
            rm = "explain"
        elif mode == "clarify":
            rm = "clarify"
        else:
            rm = "conversational"
    if mode == "meta" and rm == "conversational":
        rm = "explain"

    core = ccore.build_core(st, response_mode=rm, identity_hint=identity_hint)
    max_tok, soft_cap, _ = core.budget()
    meta["response_mode"] = core.response_mode
    meta["max_tokens"] = max_tok

    system = _SYSTEM + "\n\n## Colleague Core（仅本会话）\n" + ccore.render_context_block(core)
    if mode == "meta":
        system += (
            "\n本轮用户在问你是谁/能干什么：用两三句说清楚即可——"
            "能查已上线周报，不能读草稿；平常怎么问同事就怎么问。"
        )

    hist = _history_block(core.recent_turns)
    user = (hist + "\n\n" if hist else "") + f"用户：{q}\nMesh："

    try:
        from .. import llm

        text = llm.call(
            system,
            user,
            max_tokens=max_tok,
            json_mode=False,
            task="answer",
        )
        out = str(text or "").strip()
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        if out:
            # 仅防失控；正常对话不应触达 soft_cap
            if soft_cap and len(out) > soft_cap:
                out = out[: soft_cap - 12].rstrip() + "…"
            return out, meta
    except Exception as e:
        log.warning("colleague_chat llm failed: %s", e)
        meta["error"] = str(e)[:120]

    return _fallback(q, mode=mode, response_mode=core.response_mode), meta


def _fallback(q: str, *, mode: str, response_mode: str = "") -> str:
    if mode == "meta":
        if any(k in q for k in ("能干", "能做", "能查", "帮助", "怎么问")):
            return "我可以帮你查已上线周报里的人和事。平常怎么问同事就怎么问我就行。"
        return "我是 Mesh，GeekPark 的内部同事。问已上线周报里的人或事就行。"
    if response_mode == "clarify":
        return "你具体指哪一块？我接着说。"
    if any(k in q for k in ("谢谢", "感谢")):
        return "不客气。"
    if any(k in q for k in ("好的", "明白", "收到", "知道了")):
        return "好。"
    if "忙" in q:
        return "懂，这种节奏是挺磨人。有想对一下的人或事直接丢过来。"
    if any(k in q for k in ("机械", "机器人", "客服", "傻子")):
        return "说得对，刚才那下是偏机械了。我按同事聊天来，你继续说。"
    if "同事" in q:
        return "行，我就按内部同事这个位置来聊。你刚才那句想接着往下说吗？"
    if any(k in q for k in ("你好", "早", "嗨", "在吗")):
        return "在，说吧。"
    return "嗯，我在听。你接着说。"
