"""Colleague chat — 1× Conversation LLM（非企业事实路径）。

硬约束：
- 不查 Published / 不造公司事实
- 公司事实话术必须引导用户去问人或公司名（由 Ask 路径回答）
- 失败时短回落，不抛给用户堆栈
"""
from __future__ import annotations

import logging
from typing import Any

from .session_state import SessionContextState

log = logging.getLogger("uvicorn.error")

_SYSTEM = """你是 GeekPark（极客公园）内部的 AI 同事「Mesh」。

你在飞书里和员工说话。风格：自然、直接、熟悉媒体/科技语境；有适度情绪，但不客服腔、不官腔、不过度热情、不写长篇。

硬边界：
1) 你可以闲聊、讨论观点、帮润色/改写、表达判断与建议。
2) 你不能编造「某同事/某公司在周报里怎样」这类公司事实。若用户在问周报事实，用一两句自然说明你需要人名/公司名，或请他们直接问「××最近跟谁聊过」。
3) 不要教用户写 Prompt，不要列长问法清单，不要说「我是检索机器人」。
4) 回复尽量短：通常 1～3 句。用户只说谢谢/好的时，简短回应即可。
5) 不要每次都追问「还有什么可以帮您」；只有明确有帮助时才给一个自然下一步。
6) 不要强行 emoji。"""


def _history_block(state: SessionContextState | None) -> str:
    if not state or not state.recent_turns:
        return ""
    lines = []
    for t in (state.recent_turns or [])[-8:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        lines.append(f"{role}：{t.get('text') or ''}")
    return "最近对话：\n" + "\n".join(lines)


def reply_colleague(
    user_text: str,
    state: SessionContextState | None = None,
    *,
    mode: str = "chat",
    identity_hint: str = "",
) -> tuple[str, dict[str, Any]]:
    """返回 (text, meta)。meta 含 model / llm_used / error。"""
    meta: dict[str, Any] = {"llm_used": False, "model": None, "error": ""}
    q = (user_text or "").strip()
    if not q:
        return "嗯？", meta

    # 极短确认：仍走 LLM 优先；失败才模板（满足「不是无 AI」）
    system = _SYSTEM
    if mode == "meta":
        system += "\n当前用户在问你是谁/能干什么：用两三句介绍自己即可，强调能查已上线周报，但不能读草稿。"
    if identity_hint:
        system += f"\n对方身份提示：{identity_hint}"

    hist = _history_block(state)
    user = (hist + "\n\n" if hist else "") + f"用户：{q}\nMesh："

    try:
        from .. import llm

        text = llm.call(
            system,
            user,
            max_tokens=350,
            json_mode=False,
            task="answer",
        )
        out = str(text or "").strip()
        meta["llm_used"] = True
        meta["model"] = llm.model_for_task("answer")
        if out:
            # 截断过长
            if len(out) > 600:
                out = out[:580].rstrip() + "…"
            return out, meta
    except Exception as e:
        log.warning("colleague_chat llm failed: %s", e)
        meta["error"] = str(e)[:120]

    return _fallback(q, mode=mode), meta


def _fallback(q: str, *, mode: str) -> str:
    if mode == "meta":
        if any(k in q for k in ("能干", "能做", "能查", "帮助", "怎么问")):
            return "我可以帮你查已上线周报里的人和事。平常怎么问同事就怎么问我就行。"
        return "我是 Mesh，GeekPark 的周报助手。问已上线周报里的人或事就行。"
    if any(k in q for k in ("谢谢", "感谢")):
        return "不客气～"
    if any(k in q for k in ("好的", "明白", "收到", "知道了")):
        return "好。"
    if "忙" in q:
        return "还行，有周报的事随时问。"
    if any(k in q for k in ("你好", "早", "嗨", "在吗")):
        return "你好，我是 Mesh。想查周报直接说人名或公司就行。"
    return "嗯，我在。有事直接说。"
