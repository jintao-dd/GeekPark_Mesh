"""Colleague Controller — Stage 1 决策中枢。

统一产出 mode / intent / entities / topic / needs_* / response_mode / context_refs。
只负责判断，不生成最终回答，不产出企业事实。

预算：
  明显请求 → deterministic fast-path → 0 Router LLM
  边界模糊 → 最多 1× Controller LLM（task=controller）
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from . import conversation as conv
from .session_state import SessionContextState

log = logging.getLogger("uvicorn.error")

MODES = frozenset(
    {
        "conversation",
        "content",
        "enterprise",
        "followup",
        "clarify",
        "meta",
        "system",
    }
)
RESPONSE_MODES = frozenset(
    {
        "direct",
        "conversational",
        "clarify",
        "abstain",
        "explain",
        "compare",
        "brainstorm",
        "rewrite",
        "followup",
    }
)

_GROUNDING_STRONG = re.compile(
    r"("
    r"跟谁聊|和谁聊|接触过|沟通过|对接过|见了谁|谁见了|"
    r"有哪些关系|双边|交叉|联动|"
    r"最近进展|最近动作|在推进什么|有什么进展|什么进展|进展如何|进展怎样|"
    r"已上线|周报里|哪一期|期次|"
    r"有没有跟|有没有和|有没有接触"
    r")",
    re.I,
)

# 边界模糊 / 真实人话 → Controller LLM
_BOUNDARY_FUZZY = re.compile(
    r"("
    r"^最近怎么样[?？!！。.\s]*$|"
    r"^这个靠谱吗[?？!！。.\s]*$|"
    r"^靠谱吗[?？!！。.\s]*$|"
    r"^你怎么看[?？!！。.\s]*$|"
    r"^那个呢[?？!！。.\s]*$|"
    r"^还有吗[?？!！。.\s]*$|"
    r"我想看看.{0,12}那边|"
    r"今天事情好多|"
    r"事情真多|"
    r"感觉怎么样|"
    r"咋样了|"
    r"靠谱不|"
    r"有戏吗|"
    r"值得跟吗|"
    r"帮我瞅一眼|"
    r"瞅瞅|"
    r"瞄一眼"
    r")",
    re.I,
)

# 可确定性处理的软边界（0 LLM，仍进统一 Controller 结构）
_SOFT_CHAT = re.compile(
    r"(今天事情好多|事情真多|好累|忙死了|累死了)",
    re.I,
)
_SOFT_OPINION_BARE = re.compile(
    r"^(这个靠谱吗|靠谱吗|靠谱不|有戏吗|值得跟吗|感觉怎么样|咋样了)[?？!！。.\s]*$",
    re.I,
)
_SOFT_VAGUE_WORTH = re.compile(
    r"(这事儿|这件事|这个事).{0,12}(怎么看|值不值得|靠谱)",
    re.I,
)
_SOFT_LOOK = re.compile(
    r"我想看看\s*(?P<name>[\u4e00-\u9fffA-Za-z0-9·．\.]{2,16}?)\s*那边",
    re.I,
)

_CONTENT_HINT = re.compile(
    r"(帮我润色|润色一下|帮我改(?:一下)?(?:文案|措辞|说法|标题)|帮我写|帮我整理|总结一下刚才)",
    re.I,
)

_HIGH_CONF_NOTES = frozenset(
    {
        "empty",
        "draft_raw",
        "meta",
        "whoami",
        "capability_boundary",
        "general_chat",
        "repair",
        "list",
        "relations",
        "how_is_clarify",
        "ambiguous_bare",
        "followup_named",
        "followup_pronoun",
        "followup_later_or_more",
        "followup_team_or_entity",
        "followup_project",
        "followup_which_issue",
        "followup_project_no_ctx",
        "followup_he_no_entity",
        "followup_later_no_ctx",
        "followup_issue_no_ctx",
    }
)

_SYSTEM = """你是 GeekPark Mesh 的 Colleague Controller。
只做路由决策，不回答用户，不编造公司事实，不写长文。

根据用户这句话 + 会话摘要，输出一个 JSON 对象，字段严格如下：
{
  "mode": "conversation|content|enterprise|followup|clarify|meta|system",
  "intent": "short_snake_case",
  "entities": ["可选实体"],
  "topic": "short_topic_or_empty",
  "needs_grounding": true/false,
  "needs_clarification": true/false,
  "response_mode": "direct|conversational|clarify|abstain|explain|compare|brainstorm|rewrite|followup",
  "rewritten_query": "若 followup/enterprise 需要检索时的改写问句，否则空字符串",
  "clarify_hint": "若 needs_clarification，给一句澄清问法，否则空",
  "notes": "short_reason"
}

规则：
1) 闲聊/吐槽/致谢/观点讨论 → mode=conversation，needs_grounding=false
2) 润色/改写/整理文案 → mode=content，needs_grounding=false
3) 明确要查已上线周报里的人/公司/接触/进展 → mode=enterprise，needs_grounding=true
4) 指代续问且上下文够 → mode=followup，needs_grounding=true，必须 rewritten_query
5) 意图不清、缺主体、可多解 → mode=clarify，needs_clarification=true，needs_grounding=false
6) 你是谁/能干什么 → mode=meta
7) 改权限/发布/草稿原文 → mode=system
8) 宁可 clarify，也不要瞎查；宁可 conversation，也不要把闲聊当检索
只输出 JSON。"""


@dataclass
class ControllerDecision:
    mode: str = "clarify"
    intent: str = "clarify"
    entities: list[str] = field(default_factory=list)
    topic: str = ""
    needs_grounding: bool = False
    needs_clarification: bool = False
    response_mode: str = "clarify"
    context_refs: list[str] = field(default_factory=list)
    rewritten_query: str = ""
    clarify_text: str = ""
    casual_text: str = ""
    topic_frame: str = ""
    resolved_entity: str = ""
    notes: str = ""
    source: str = "fast"  # fast|llm|llm_fallback
    router_llm_used: bool = False
    router_model: str | None = None
    confidence: str = "high"  # high|low

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_route_decision(self) -> conv.RouteDecision:
        route = _mode_to_route(self.mode, self.intent)
        intent = _mode_to_runtime_intent(self.mode, self.intent)
        return conv.RouteDecision(
            route=route,
            intent=intent,
            rewritten_query=self.rewritten_query or "",
            clarify_text=self.clarify_text or "",
            casual_text=self.casual_text or "",
            topic_frame=self.topic_frame or _topic_from_fields(self.topic, self.topic_frame),
            resolved_entity=self.resolved_entity
            or (self.entities[0] if self.entities else ""),
            notes=f"ctrl:{self.source}:{self.notes or self.mode}",
        )


def _mode_to_route(mode: str, intent: str) -> str:
    m = (mode or "").strip().lower()
    i = (intent or "").strip().lower()
    if m in ("conversation", "content"):
        return "general_conversation"
    if m == "meta":
        return "meta"
    if m == "clarify":
        return "clarify"
    if m == "followup":
        return "followup"
    if m == "enterprise":
        if i in ("ask_relations", "relationship_query", "relations"):
            return "relations"
        if i in ("list_issues", "list"):
            return "list"
        return "ask"
    if m == "system":
        return "refuse"
    return "clarify"


def _mode_to_runtime_intent(mode: str, intent: str) -> str:
    m = (mode or "").strip().lower()
    i = (intent or "").strip().lower()
    if m == "meta":
        return "whoami" if i == "whoami" else "help"
    if m in ("conversation", "content"):
        return "casual"
    if m == "clarify":
        return "clarify"
    if m == "system":
        return "refuse"
    if m == "followup":
        if i in ("ask_relations", "relationship_query", "relations"):
            return "ask_relations"
        return "ask_published"
    if m == "enterprise":
        if i in ("list_issues", "list"):
            return "list_issues"
        if i in ("ask_relations", "relationship_query", "relations"):
            return "ask_relations"
        return "ask_published"
    return "clarify"


def _topic_from_fields(topic: str, frame: str = "") -> str:
    t = (topic or "").strip().lower()
    if frame:
        return frame
    if "contact" in t or "接触" in t or t == "business_contact":
        return "contact"
    if "progress" in t or "进展" in t:
        return "progress"
    if "relation" in t:
        return "relations"
    return "about"


def _controller_llm_enabled() -> bool:
    v = (os.environ.get("MESH_COLLEAGUE_CONTROLLER_LLM") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def _has_strong_grounding(q: str) -> bool:
    return bool(_GROUNDING_STRONG.search(q or ""))


def _is_boundary_fuzzy(q: str) -> bool:
    return bool(_BOUNDARY_FUZZY.search(q or ""))


def _session_context_block(state: SessionContextState | None) -> str:
    st = state or SessionContextState()
    ents = "、".join((st.active_entities or [])[:5]) or "（无）"
    return (
        f"active_entities={ents}\n"
        f"active_topic={st.active_topic or ''}\n"
        f"last_topic_frame={st.last_topic_frame or ''}\n"
        f"last_query={st.last_query or ''}\n"
        f"conversation_mode={st.conversation_mode or ''}\n"
        f"active_issue={st.active_issue or ''}\n"
        f"unresolved={';'.join(st.unresolved_references or [])[:80]}"
    )


def from_route_decision(
    route: conv.RouteDecision,
    text: str,
    state: SessionContextState | None = None,
    *,
    source: str = "fast",
    confidence: str = "high",
) -> ControllerDecision:
    st = state or SessionContextState()
    q = conv.normalize_query(text)
    ents = list(
        dict.fromkeys(
            ([route.resolved_entity] if route.resolved_entity else [])
            + conv.extract_entities_from_text(q)
            + list(st.active_entities or [])[:3]
        )
    )
    ents = [e for e in ents if e][:6]

    mode = "clarify"
    needs_g = False
    needs_c = False
    resp = "clarify"
    topic = route.topic_frame or ""
    intent = route.intent or ""

    if route.route == "meta" or route.intent in ("help", "whoami"):
        mode, resp, intent = "meta", "conversational", route.intent
    elif route.route == "general_conversation" or route.intent == "casual":
        if _CONTENT_HINT.search(q):
            mode, resp, intent = "content", "rewrite", "content_assist"
        else:
            mode, resp, intent = "conversation", "conversational", "casual"
    elif route.route == "clarify" or route.intent == "clarify":
        mode, needs_c, resp, intent = "clarify", True, "clarify", "clarify"
    elif route.route == "followup":
        mode, needs_g, resp = "followup", True, "followup"
        intent = route.intent or "ask_published"
        topic = topic or "business_contact"
    elif route.route in ("ask", "relations", "list"):
        mode, needs_g, resp = "enterprise", True, "direct"
        intent = route.intent or "ask_published"
        if route.route == "relations":
            topic = "relations"
        elif route.route == "list":
            topic = "issue_list"
        else:
            topic = topic or "business"
    elif route.route == "refuse" or route.intent == "refuse":
        mode, resp, intent = "system", "abstain", "refuse"

    refs: list[str] = []
    if st.active_entities:
        refs.append("active_entities")
    if st.last_query:
        refs.append("last_query")
    if st.active_issue:
        refs.append("active_issue")
    if route.rewritten_query:
        refs.append("rewritten_query")

    return ControllerDecision(
        mode=mode,
        intent=intent,
        entities=ents,
        topic=topic,
        needs_grounding=needs_g,
        needs_clarification=needs_c,
        response_mode=resp,
        context_refs=refs,
        rewritten_query=route.rewritten_query or "",
        clarify_text=route.clarify_text or "",
        casual_text=route.casual_text or "",
        topic_frame=route.topic_frame or "",
        resolved_entity=route.resolved_entity or "",
        notes=route.notes or "",
        source=source,
        router_llm_used=False,
        confidence=confidence,
    )


def _fast_path_confidence(route: conv.RouteDecision, q: str) -> str:
    notes = (route.notes or "").strip()
    if notes in _HIGH_CONF_NOTES or notes.startswith("resume_"):
        return "high"
    if route.route in ("meta", "refuse") or route.intent in ("help", "whoami", "refuse"):
        return "high"
    if route.route == "followup" and route.rewritten_query:
        return "high"
    if route.route == "clarify":
        return "high"
    if route.route == "general_conversation":
        return "high"
    if route.route in ("list", "relations"):
        return "high"
    if route.route == "ask" and _has_strong_grounding(q):
        return "high"
    if route.route == "ask" and conv.extract_entities_from_text(q) and re.search(
        r"(最近|本期|周报|接触|聊|对接|进展|关系)", q
    ):
        return "high"
    return "low"


def will_retrieve(decision: ControllerDecision) -> bool:
    return bool(decision.needs_grounding) and decision.mode in ("enterprise", "followup")


def decide(
    text: str,
    state: SessionContextState | None = None,
    *,
    allow_llm: bool | None = None,
) -> ControllerDecision:
    """统一决策入口。"""
    st = state or SessionContextState()
    q = conv.normalize_query(text)
    use_llm = _controller_llm_enabled() if allow_llm is None else bool(allow_llm)

    soft = _soft_boundary_decide(q, st)
    if soft is not None:
        return soft

    route = conv.route_message(q, st)
    conf = _fast_path_confidence(route, q)

    # 高置信 fast-path：0 Router LLM
    if conf == "high":
        return from_route_decision(route, q, st, source="fast", confidence="high")

    if not use_llm:
        if route.route == "ask" and not _has_strong_grounding(q):
            return ControllerDecision(
                mode="clarify",
                intent="clarify",
                entities=conv.extract_entities_from_text(q),
                needs_grounding=False,
                needs_clarification=True,
                response_mode="clarify",
                clarify_text=conv._clarify_bare(q, st),
                notes="low_conf_no_llm",
                source="fast",
                confidence="low",
            )
        return from_route_decision(route, q, st, source="fast", confidence=conf)

    llm_dec = _llm_decide(q, st)
    if llm_dec is not None:
        return llm_dec

    if route.route == "ask" and not _has_strong_grounding(q):
        d = from_route_decision(route, q, st, source="llm_fallback", confidence="low")
        d.mode = "clarify"
        d.intent = "clarify"
        d.needs_grounding = False
        d.needs_clarification = True
        d.response_mode = "clarify"
        d.clarify_text = d.clarify_text or conv._clarify_bare(q, st)
        d.notes = "llm_fail_clarify"
        return d
    return from_route_decision(route, q, st, source="llm_fallback", confidence="low")


def _soft_boundary_decide(q: str, st: SessionContextState) -> ControllerDecision | None:
    """有限软边界：真实人话但可确定性决策（仍 0 Router LLM）。"""
    if _SOFT_CHAT.search(q):
        return ControllerDecision(
            mode="conversation",
            intent="casual",
            needs_grounding=False,
            needs_clarification=False,
            response_mode="conversational",
            notes="soft_chat",
            source="fast",
            confidence="high",
        )
    if _SOFT_OPINION_BARE.match(q) or _SOFT_VAGUE_WORTH.search(q):
        # 缺主体的「靠谱/值不值得」→ 澄清，不瞎查
        return ControllerDecision(
            mode="clarify",
            intent="clarify",
            needs_grounding=False,
            needs_clarification=True,
            response_mode="clarify",
            clarify_text="你说的是哪个人或哪家公司？说名字我按已上线周报看。",
            notes="soft_opinion_bare",
            source="fast",
            confidence="high",
        )
    m = _SOFT_LOOK.match(q)
    if m:
        name = (m.group("name") or "").strip()
        if name and name not in conv._STOP:
            # 软企业意图：需要 grounding，但先可 clarify 维度；Stage 1 直接 enterprise
            return ControllerDecision(
                mode="enterprise",
                intent="ask_published",
                entities=[name],
                topic="business_contact",
                needs_grounding=True,
                needs_clarification=False,
                response_mode="direct",
                rewritten_query=f"{name}最近相关的接触或进展",
                resolved_entity=name,
                topic_frame="contact",
                notes="soft_look_entity",
                source="fast",
                confidence="high",
            )
    return None


def _llm_decide(q: str, st: SessionContextState) -> ControllerDecision | None:
    try:
        from .. import llm

        user = (
            "会话摘要：\n"
            + _session_context_block(st)
            + "\n\n用户说：\n"
            + q
            + "\n\n只输出 JSON。"
        )
        data = llm.call(_SYSTEM, user, max_tokens=350, json_mode=True, task="controller")
        if not isinstance(data, dict):
            return None
        return _parse_llm_payload(data, q, st, model=llm.model_for_task("controller"))
    except Exception as e:
        log.warning("colleague_controller llm failed: %s", e)
        return None


def _parse_llm_payload(
    data: dict[str, Any],
    q: str,
    st: SessionContextState,
    *,
    model: str | None = None,
) -> ControllerDecision:
    mode = str(data.get("mode") or "clarify").strip().lower()
    if mode not in MODES:
        mode = "clarify"
    resp = str(data.get("response_mode") or "clarify").strip().lower()
    if resp not in RESPONSE_MODES:
        resp = "clarify" if mode == "clarify" else "conversational"

    ents_raw = data.get("entities") or []
    entities: list[str] = []
    if isinstance(ents_raw, list):
        for e in ents_raw:
            s = str(e or "").strip()
            if s and s not in entities:
                entities.append(s)
    entities = entities[:6]

    needs_g = bool(data.get("needs_grounding"))
    needs_c = bool(data.get("needs_clarification"))
    if mode in ("conversation", "content", "meta", "clarify", "system"):
        needs_g = False
    if mode == "clarify":
        needs_c = True
    if mode in ("enterprise", "followup"):
        needs_g = True
        needs_c = False

    rewritten = str(data.get("rewritten_query") or "").strip()
    if mode == "followup" and not rewritten:
        ent = entities[0] if entities else (st.active_entities[0] if st.active_entities else "")
        if ent:
            rewritten = conv._rewrite_for_entity(
                ent, st.last_topic_frame or "about", st.last_query or ""
            )
        else:
            mode = "clarify"
            needs_g = False
            needs_c = True

    clarify_hint = str(data.get("clarify_hint") or data.get("clarify_text") or "").strip()
    if needs_c and not clarify_hint:
        clarify_hint = conv._clarify_bare(q, st)

    intent = str(data.get("intent") or "").strip() or mode
    topic = str(data.get("topic") or "").strip()
    notes = str(data.get("notes") or "llm").strip()[:80]

    return ControllerDecision(
        mode=mode,
        intent=intent,
        entities=entities,
        topic=topic,
        needs_grounding=needs_g,
        needs_clarification=needs_c,
        response_mode=resp,
        context_refs=["session_summary", "user_utterance"],
        rewritten_query=rewritten,
        clarify_text=clarify_hint,
        topic_frame=_topic_from_fields(topic, st.last_topic_frame or ""),
        resolved_entity=entities[0] if entities else "",
        notes=notes,
        source="llm",
        router_llm_used=True,
        router_model=model,
        confidence="low",
    )
