"""Colleague Core — Stage 2A session-scoped context（非长期 Memory）。

Controller 只决定 response_mode；本模块负责「同事怎么理解和回应」。
禁止：Vector Memory、跨 session 持久化用户画像、额外 LLM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .session_state import SessionContextState

# response_mode → (max_tokens, soft_char_cap, style_hint)
# soft_char_cap 仅防失控；远高于旧 600 硬砍，正常对话不应触达。
RESPONSE_BUDGETS: dict[str, tuple[int, int, str]] = {
    "direct": (
        220,
        480,
        "短、结论优先；一两句说完即可，不要铺垫。",
    ),
    "conversational": (
        480,
        1400,
        "像飞书里跟同事自然聊；可有情绪与节奏，长短按内容需要，不要刻意写成小作文，也不要机械压成一句话。",
    ),
    "opinion": (
        650,
        1800,
        "允许「我觉得 / 我更倾向 / 如果是我」；先观点再短理由。明确这是观点不是公司事实。",
    ),
    "clarify": (
        180,
        360,
        "一两句问清楚关键缺口；不要盘问、不要列选项清单、不要解释自己能力。",
    ),
    "explain": (
        700,
        2000,
        "按需要展开，结构清楚；仍像同事口述，不要 PPT 腔。",
    ),
    "rewrite": (
        800,
        2500,
        "直接给出改写/润色结果；最多一句说明，不要长篇解释改了什么。",
    ),
    "followup": (
        400,
        1200,
        "承接前文；不要复述用户刚说过的整段话；不要重开话题说明书。",
    ),
    "abstain": (
        220,
        480,
        "自然说明做不到或不该编的部分；不暴露内部路由/检索/权限字段。",
    ),
}

_DEFAULT_IDENTITY = "GeekPark Mesh（内部 AI 同事）"
_DEFAULT_ROLE = "在飞书里协助同事聊工作语境、观点与已上线周报相关问题"
_DEFAULT_STYLE = "自然、直接、熟悉媒体/科技语境；像内部同事，不像客服或检索机器人"


@dataclass
class ColleagueCoreContext:
    self_identity: str = _DEFAULT_IDENTITY
    role: str = _DEFAULT_ROLE
    communication_style: str = _DEFAULT_STYLE
    relationship_context: str = "internal-colleague"
    user_tone: str = "neutral"
    current_emotional_tone: str = "neutral"
    current_goal: str = ""
    current_topic: str = ""
    recent_turns: list[dict[str, Any]] = field(default_factory=list)
    user_feedback: str = ""
    response_mode: str = "conversational"

    def budget(self) -> tuple[int, int, str]:
        return RESPONSE_BUDGETS.get(
            self.response_mode, RESPONSE_BUDGETS["conversational"]
        )


_FEEDBACK_RE = re.compile(
    r"(机械|机器人|客服|官腔|太正式|不像人|傻子|智障|蠢|废话|"
    r"像同事|正常点|自然点|别客服|别那么正式|别解释那么多)",
    re.I,
)
_FRUSTRATED = re.compile(r"(烦|受够|无语|服了|崩溃|气死|搞什么|什么玩意|傻)", re.I)
_EXCITED = re.compile(r"(太好了|牛|赞|惊喜|兴奋|哈哈+|嘿嘿)", re.I)
_CASUAL = re.compile(r"(忙死了|累死|随便|咋样|咋整|搞定|摸鱼)", re.I)
_SERIOUS = re.compile(r"(认真|严肃|重要|务必|慎重|决策)", re.I)
_PREF_COLLEAGUE = re.compile(
    r"(像同事|成为.{0,8}同事|别像机器人|不要像机器人|像人一样|正常聊天)", re.I
)
_PREF_SHORT = re.compile(r"(别那么长|简洁点|短一点|少解释)", re.I)


def infer_tones(user_text: str) -> tuple[str, str]:
    """返回 (user_tone, emotional_tone)。纯启发式，0 LLM。"""
    q = (user_text or "").strip()
    if not q:
        return "neutral", "neutral"
    if _FRUSTRATED.search(q):
        return "frustrated", "frustrated"
    if _EXCITED.search(q):
        return "casual", "excited"
    if _CASUAL.search(q):
        return "casual", "casual"
    if _SERIOUS.search(q):
        return "serious", "serious"
    if _FEEDBACK_RE.search(q):
        return "corrective", "frustrated" if any(
            x in q for x in ("傻", "机械", "机器人", "客服")
        ) else "neutral"
    return "neutral", "neutral"


def apply_user_signal(state: SessionContextState, user_text: str) -> None:
    """Session 内更新 relationship / feedback / tone。不跨 session。"""
    q = (user_text or "").strip()
    if not q:
        return
    tone, emo = infer_tones(q)
    state.colleague_user_tone = tone
    state.colleague_emotional_tone = emo

    if _FEEDBACK_RE.search(q):
        state.colleague_feedback = q[:200]
    if _PREF_COLLEAGUE.search(q):
        state.colleague_relationship = "familiar-colleague"
        pref = (state.colleague_pref or "").strip()
        note = "用户希望像真实同事，不要机器人/客服腔"
        if note not in pref:
            state.colleague_pref = (pref + "；" + note).strip("；")[:240]
    if _PREF_SHORT.search(q):
        pref = (state.colleague_pref or "").strip()
        note = "用户偏好更短、少解释"
        if note not in pref:
            state.colleague_pref = (pref + "；" + note).strip("；")[:240]

    # 轻量 goal / topic
    if any(k in q for k in ("润色", "改写", "说自然", "帮我把这句")):
        state.colleague_goal = "rewrite"
    elif any(k in q for k in ("怎么看", "你觉得", "值不值得", "更倾向")):
        state.colleague_goal = "opinion"
    elif emo == "frustrated" or tone == "corrective":
        state.colleague_goal = "repair"
    elif state.colleague_goal in ("repair",) and tone in ("casual", "neutral", "excited"):
        state.colleague_goal = "continue"


def build_core(
    state: SessionContextState | None,
    *,
    response_mode: str = "conversational",
    identity_hint: str = "",
) -> ColleagueCoreContext:
    st = state or SessionContextState()
    mode = (response_mode or "conversational").strip().lower()
    if mode not in RESPONSE_BUDGETS:
        mode = "conversational"
    style = _DEFAULT_STYLE
    pref = (st.colleague_pref or "").strip()
    if pref:
        style = f"{_DEFAULT_STYLE} 当前偏好：{pref}"
    rel = (st.colleague_relationship or "internal-colleague").strip() or "internal-colleague"
    topic = (st.active_topic or "").strip()
    if not topic and st.recent_turns:
        last_u = next(
            (t for t in reversed(st.recent_turns) if t.get("role") == "user"),
            None,
        )
        if last_u:
            topic = str(last_u.get("text") or "")[:40]
    return ColleagueCoreContext(
        self_identity=_DEFAULT_IDENTITY,
        role=_DEFAULT_ROLE
        + (f"；对方：{identity_hint}" if identity_hint else ""),
        communication_style=style,
        relationship_context=rel,
        user_tone=(st.colleague_user_tone or "neutral"),
        current_emotional_tone=(st.colleague_emotional_tone or "neutral"),
        current_goal=(st.colleague_goal or ""),
        current_topic=topic,
        recent_turns=list(st.recent_turns or [])[-8:],
        user_feedback=(st.colleague_feedback or ""),
        response_mode=mode,
    )


def render_context_block(core: ColleagueCoreContext) -> str:
    lines = [
        f"身份：{core.self_identity}（用户没问就不要自我介绍）",
        f"角色：{core.role}",
        f"关系：{core.relationship_context}（仅本会话）",
        f"表达风格：{core.communication_style}",
        f"用户语气：{core.user_tone}；当前情绪：{core.current_emotional_tone}",
        f"response_mode：{core.response_mode}",
    ]
    if core.current_goal:
        lines.append(f"当前目标：{core.current_goal}")
    if core.current_topic:
        lines.append(f"当前话题：{core.current_topic}")
    if core.user_feedback:
        lines.append(f"用户近期反馈（须吸收，勿辩解）：{core.user_feedback}")
    hint = core.budget()[2]
    lines.append(f"本轮表达预算：{hint}")
    return "\n".join(lines)
