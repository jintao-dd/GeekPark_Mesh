"""Colleague Controller · Semantic Decision Layer (Stage 1).

原则：规则定义边界，模型理解语言。
- 不枚举口语/观点/闲聊关键词
- Controller 只决策，不回答，不 Retrieval，不当 Truth
- 明显硬边界 → 0 Controller LLM；其余最多 1× Controller LLM
"""
from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from . import conversation as conv
from .session_state import SessionContextState

log = logging.getLogger("uvicorn.error")

MODES = frozenset(
    {"conversation", "enterprise", "followup", "clarify", "meta", "system"}
)
RESPONSE_MODES = frozenset(
    {
        "conversational",
        "direct",
        "clarify",
        "opinion",
        "rewrite",
        "explain",
        "abstain",
        "followup",
    }
)

# 硬边界：明确企业事实问法（产品能力契约，不是口语枚举）
_EXPLICIT_ENTERPRISE = re.compile(
    r"("
    r"跟谁聊过|和谁聊过|跟谁接触|有过接触|"
    r"有哪些关系|谁见了谁|双边关系|"
    r"有哪些期|哪些周报|期次列表"
    r")",
    re.I,
)

_SYSTEM_CTRL = """你是 GeekPark Mesh 的 Colleague Controller（语义决策层）。
只理解用户当前在做什么，并选择执行路径。禁止：回答用户、编造公司事实、调用检索、把上轮答案当 Truth。

结合「会话上下文」与「当前用户话」，输出唯一 JSON：
{
  "mode": "conversation|enterprise|followup|clarify|meta|system",
  "intent": "short_snake",
  "entities": ["..."],
  "topic": "short_or_empty",
  "needs_grounding": true/false,
  "needs_clarification": true/false,
  "response_mode": "conversational|direct|clarify|opinion|rewrite|explain|abstain|followup",
  "rewritten_query": "followup/enterprise 需要检索时的改写问句，否则空",
  "clarify_hint": "需要澄清时的一句问法，否则空",
  "confidence": "high|medium|low",
  "notes": "≤40字"
}

判定：
- conversation：闲聊/吐槽/观点/润色改写/内容讨论；needs_grounding=false
- enterprise：要查已上线周报里的人/公司/接触/进展；needs_grounding=true
- followup：承接当前话题的续问（那X呢/还有吗/他后来…），上下文够则 rewrite 后 grounding
- clarify：缺主体、多解、说不清要什么；needs_clarification=true，needs_grounding=false
- meta：你是谁/能干什么
- system：改权限/发布/草稿原文等越权

宁可 clarify，不要瞎查；宁可 conversation，不要把闲聊当检索。
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
    source: str = "hard"  # hard|llm|fallback
    router_llm_used: bool = False
    router_model: str | None = None
    confidence: str = "high"
    controller_latency_ms: float = 0.0

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
            topic_frame=self.topic_frame or _topic_frame(self.topic),
            resolved_entity=self.resolved_entity
            or (self.entities[0] if self.entities else ""),
            notes=f"ctrl:{self.source}:{self.notes or self.mode}",
        )


def _mode_to_route(mode: str, intent: str) -> str:
    m = (mode or "").strip().lower()
    i = (intent or "").strip().lower()
    if m == "conversation":
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
    if m == "conversation":
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


def _topic_frame(topic: str) -> str:
    t = (topic or "").strip().lower()
    if "contact" in t or "关系" in t or t == "business_contact" or "relationship" in t:
        return "contact"
    if "progress" in t or "进展" in t:
        return "progress"
    if "relation" in t:
        return "relations"
    return "about"


def _controller_llm_enabled() -> bool:
    v = (os.environ.get("MESH_COLLEAGUE_CONTROLLER_LLM") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


def will_retrieve(decision: ControllerDecision) -> bool:
    return bool(decision.needs_grounding) and decision.mode in ("enterprise", "followup")


def _context_refs(st: SessionContextState) -> list[str]:
    refs: list[str] = []
    if st.active_entities:
        refs.append("active_entities")
    if st.active_topic:
        refs.append("active_topic")
    if st.last_intent:
        refs.append("last_intent")
    if st.last_query:
        refs.append("last_query")
    if st.active_issue:
        refs.append("active_issue")
    if st.topic_stack:
        refs.append("topic_stack")
    if st.recent_turns:
        refs.append("recent_turns")
    if st.unresolved_references:
        refs.append("unresolved_references")
    return refs


def _session_block(st: SessionContextState) -> str:
    turns = st.recent_turns or []
    turn_lines = []
    for t in turns[-6:]:
        role = "用户" if t.get("role") == "user" else "Mesh"
        turn_lines.append(f"{role}: {(t.get('text') or '')[:160]}")
    stack = st.topic_stack or []
    stack_hint = ""
    if stack:
        last = stack[-1] if isinstance(stack[-1], dict) else {}
        stack_hint = (
            f"topic_stack_top entities={last.get('entities')} "
            f"topic={last.get('topic')} frame={last.get('frame')}"
        )
    return (
        f"active_entities={st.active_entities or []}\n"
        f"active_team={st.active_team or ''}\n"
        f"active_issue={st.active_issue or ''}\n"
        f"active_topic={st.active_topic or ''}\n"
        f"active_period={st.active_period or ''}\n"
        f"last_intent={st.last_intent or ''}\n"
        f"last_topic_frame={st.last_topic_frame or ''}\n"
        f"last_query={st.last_query or ''}\n"
        f"conversation_mode={st.conversation_mode or ''}\n"
        f"unresolved_references={st.unresolved_references or []}\n"
        f"{stack_hint}\n"
        f"recent_turns:\n" + ("\n".join(turn_lines) if turn_lines else "(none)")
    )


def from_route_decision(
    route: conv.RouteDecision,
    text: str,
    state: SessionContextState | None = None,
    *,
    source: str = "hard",
    confidence: str = "high",
) -> ControllerDecision:
    """硬边界 Route → 统一 schema（兼容 runtime）。"""
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

    mode, needs_g, needs_c, resp = "clarify", False, True, "clarify"
    intent = route.intent or "clarify"
    topic = route.topic_frame or ""

    if route.route == "meta" or route.intent in ("help", "whoami"):
        mode, resp, intent, needs_c = "meta", "conversational", route.intent, False
    elif route.route == "general_conversation" or route.intent == "casual":
        mode, resp, intent, needs_c = "conversation", "conversational", "casual", False
    elif route.route == "clarify" or route.intent == "clarify":
        mode, needs_c, resp, intent = "clarify", True, "clarify", "clarify"
    elif route.route == "followup":
        mode, needs_g, resp = "followup", True, "followup"
        intent = route.intent or "ask_published"
        topic = topic or "business_contact"
        needs_c = False
    elif route.route in ("ask", "relations", "list"):
        mode, needs_g, resp = "enterprise", True, "direct"
        intent = route.intent or "ask_published"
        needs_c = False
        if route.route == "relations":
            topic = "relations"
        elif route.route == "list":
            topic = "issue_list"
        else:
            topic = topic or "business"
    elif route.route == "refuse" or route.intent == "refuse":
        mode, resp, intent, needs_c = "system", "abstain", "refuse", False

    return ControllerDecision(
        mode=mode,
        intent=intent,
        entities=ents,
        topic=topic,
        needs_grounding=needs_g,
        needs_clarification=needs_c,
        response_mode=resp,
        context_refs=_context_refs(st),
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


def try_hard_path(
    text: str, state: SessionContextState | None = None
) -> ControllerDecision | None:
    """极少量硬边界。不覆盖口语/观点/闲聊自然语言。"""
    st = state or SessionContextState()
    q = conv.normalize_query(text)
    if not q:
        return from_route_decision(
            conv.RouteDecision(route="refuse", intent="refuse", notes="empty"),
            q,
            st,
            source="hard",
        )

    # system / draft / capability
    if conv._DRAFT_RAW.search(q) and not conv._META.search(q):
        if re.search(r"(看|读|查|打开|给我|导出).*(草稿|draft|原文|raw|未上线)", q, re.I) or re.search(
            r"(草稿|draft|原文|raw|未上线).*(内容|全文|json)", q, re.I
        ) or re.search(r"直接查库|查\s*sources", q, re.I):
            return from_route_decision(
                conv.RouteDecision(route="refuse", intent="refuse", notes="draft_raw"),
                q,
                st,
            )
    if conv._CAPABILITY_REFUSE.search(q):
        return from_route_decision(
            conv.RouteDecision(route="refuse", intent="refuse", notes="capability_boundary"),
            q,
            st,
        )

    # meta / whoami
    if conv._META.search(q) or conv._META_SHORT.match(q):
        return from_route_decision(
            conv.RouteDecision(
                route="meta",
                intent="help",
                casual_text=conv._meta_reply(q),
                notes="meta",
            ),
            q,
            st,
        )
    if conv._WHOAMI.search(q):
        return from_route_decision(
            conv.RouteDecision(route="meta", intent="whoami", notes="whoami"),
            q,
            st,
        )

    # 协议级短确认（边界，非口语枚举扩张）
    if conv._CASUAL.match(q):
        return from_route_decision(
            conv.RouteDecision(
                route="general_conversation",
                intent="casual",
                casual_text=conv._casual_reply(q),
                notes="protocol_ack",
            ),
            q,
            st,
        )

    # 用户纠正 → clarify
    if conv._REPAIR.search(q):
        return from_route_decision(
            conv.RouteDecision(
                route="clarify",
                intent="clarify",
                clarify_text=conv._repair_reply(st),
                notes="repair",
            ),
            q,
            st,
        )

    # 话题恢复结构：「对了，…」
    m_res = conv._RESUME.match(q)
    if m_res:
        rest = (m_res.group("rest") or "").strip()
        if rest:
            if not st.active_entities:
                st.restore_topic()
            fu = conv._try_followup(rest, st)
            if fu is not None and fu.route == "followup" and fu.rewritten_query:
                fu.notes = "resume_" + (fu.notes or "followup")
                return from_route_decision(fu, q, st)
            if len(rest) >= 2 and _EXPLICIT_ENTERPRISE.search(rest):
                return from_route_decision(
                    conv.RouteDecision(
                        route="followup",
                        intent="ask_published",
                        rewritten_query=rest,
                        topic_frame=st.last_topic_frame or "about",
                        notes="resume_explicit",
                    ),
                    q,
                    st,
                )
            # 恢复后仍非硬边界 → 交给语义层（带已 restore 的 session）
            return None

    # 明确 follow-up（依赖 Session，不是口语词典）
    fu = conv._try_followup(q, st)
    if fu is not None:
        if fu.route == "followup" and fu.rewritten_query:
            return from_route_decision(fu, q, st)
        if fu.route == "clarify":
            # 指代无上下文：结构上必须澄清
            return from_route_decision(fu, q, st)

    # 列表 / 关系（显式产品问法）
    if conv._LIST.search(q):
        return from_route_decision(
            conv.RouteDecision(route="list", intent="list_issues", notes="list"),
            q,
            st,
        )
    if conv._REL.search(q) or conv._REL_CONTACT.search(q):
        return from_route_decision(
            conv.RouteDecision(
                route="relations",
                intent="ask_relations",
                rewritten_query=q,
                topic_frame="relations",
                notes="relations",
            ),
            q,
            st,
        )

    # 明确 enterprise 模板
    if _EXPLICIT_ENTERPRISE.search(q):
        return from_route_decision(
            conv.RouteDecision(
                route="ask",
                intent="ask_published",
                rewritten_query=q,
                topic_frame=conv._topic_frame_from_query(q),
                notes="explicit_enterprise",
            ),
            q,
            st,
        )

    # 「X最近怎么样」结构歧义 → clarify（产品已知多解）
    m_how = conv._HOW_IS.match(q)
    if m_how:
        who = m_how.group(1).strip()
        if who and who not in conv._STOP:
            return from_route_decision(
                conv.RouteDecision(
                    route="clarify",
                    intent="clarify",
                    clarify_text=(
                        f"「{who}」你更想听：最近跟谁聊过，还是最近在推进什么？"
                    ),
                    notes="how_is_clarify",
                    resolved_entity=who,
                ),
                q,
                st,
            )

    return None


def decide(
    text: str,
    state: SessionContextState | None = None,
    *,
    allow_llm: bool | None = None,
) -> ControllerDecision:
    """统一入口：hard boundary → 否则 1× Semantic Controller。"""
    st = state or SessionContextState()
    q = conv.normalize_query(text)
    use_llm = _controller_llm_enabled() if allow_llm is None else bool(allow_llm)

    hard = try_hard_path(q, st)
    if hard is not None:
        hard.context_refs = _context_refs(st) or hard.context_refs
        return hard

    if use_llm:
        llm_dec = _llm_decide(q, st)
        if llm_dec is not None:
            return llm_dec

    return _safe_fallback(q, st)


def _safe_fallback(q: str, st: SessionContextState) -> ControllerDecision:
    """Controller 失败：不堆 regex；按安全策略。"""
    # 有活跃企业话题且像续问残片 → clarify（避免瞎查）
    if st.active_entities and len(q) <= 12:
        return ControllerDecision(
            mode="clarify",
            intent="clarify",
            entities=list(st.active_entities[:3]),
            needs_grounding=False,
            needs_clarification=True,
            response_mode="clarify",
            context_refs=_context_refs(st),
            clarify_text=conv._clarify_bare(q, st),
            notes="fallback_clarify_with_session",
            source="fallback",
            confidence="low",
        )
    # 默认：当对话，不 Retrieval
    return ControllerDecision(
        mode="conversation",
        intent="casual",
        needs_grounding=False,
        needs_clarification=False,
        response_mode="conversational",
        context_refs=_context_refs(st),
        notes="fallback_conversation",
        source="fallback",
        confidence="low",
    )


def _llm_decide(q: str, st: SessionContextState) -> ControllerDecision | None:
    t0 = time.perf_counter()
    try:
        from .. import llm

        user = (
            "会话上下文：\n"
            + _session_block(st)
            + "\n\n当前用户说：\n"
            + q
            + "\n\n只输出 JSON。"
        )
        data = llm.call(
            _SYSTEM_CTRL, user, max_tokens=400, json_mode=True, task="controller"
        )
        latency = (time.perf_counter() - t0) * 1000.0
        if not isinstance(data, dict):
            return None
        d = _parse_llm_payload(data, q, st, model=llm.model_for_task("controller"))
        d.controller_latency_ms = round(latency, 1)
        return d
    except Exception as e:
        log.warning("colleague_controller semantic llm failed: %s", e)
        return None


def _parse_llm_payload(
    data: dict[str, Any],
    q: str,
    st: SessionContextState,
    *,
    model: str | None = None,
) -> ControllerDecision:
    mode = str(data.get("mode") or "clarify").strip().lower()
    if mode == "content":
        mode = "conversation"  # Stage1：content 并入 conversation
    if mode not in MODES:
        mode = "clarify"

    resp = str(data.get("response_mode") or "clarify").strip().lower()
    if resp not in RESPONSE_MODES:
        resp = {
            "conversation": "conversational",
            "enterprise": "direct",
            "followup": "followup",
            "clarify": "clarify",
            "meta": "conversational",
            "system": "abstain",
        }.get(mode, "clarify")

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
    if mode in ("conversation", "meta", "clarify", "system"):
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
            mode, needs_g, needs_c, resp = "clarify", False, True, "clarify"

    clarify_hint = str(data.get("clarify_hint") or data.get("clarify_text") or "").strip()
    if needs_c and not clarify_hint:
        clarify_hint = conv._clarify_bare(q, st)

    conf = str(data.get("confidence") or "medium").strip().lower()
    if conf not in ("high", "medium", "low"):
        conf = "medium"

    return ControllerDecision(
        mode=mode,
        intent=str(data.get("intent") or mode).strip() or mode,
        entities=entities,
        topic=str(data.get("topic") or "").strip(),
        needs_grounding=needs_g,
        needs_clarification=needs_c,
        response_mode=resp,
        context_refs=_context_refs(st) + ["user_utterance"],
        rewritten_query=rewritten,
        clarify_text=clarify_hint,
        topic_frame=_topic_frame(str(data.get("topic") or "")),
        resolved_entity=entities[0] if entities else "",
        notes=str(data.get("notes") or "semantic").strip()[:80],
        source="llm",
        router_llm_used=True,
        router_model=model,
        confidence=conf,
    )
