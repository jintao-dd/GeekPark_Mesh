"""Conversation Route — 入口决策：不是每句话都是 Search Query。

Meta / Casual / Clarify / Follow-up / Business
不改 Ranking / Claim / Retrieval；Follow-up 只 rewrite query 后走既有 Ask。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .session_state import SessionContextState

# --- patterns ---
_META = re.compile(
    r"("
    r"帮助|怎么用|使用说明|问法|怎么问|如何提问|怎么提问|"
    r"你能做什么|你能干什么|你可以做什么|你可以干什么|"
    r"你能查哪些|你能查什么|可以查什么|能力边界|"
    r"你是谁|你是什么|你是啥|你是哪位|介绍一下你(?:自己)?|"
    r"what\s+are\s+you|who\s+are\s+you|"
    r"\bhelp\b|\bcommands?\b"
    r")",
    re.I,
)
_META_SHORT = re.compile(r"^你是[?？!\s]*$", re.I)
_WHOAMI = re.compile(r"(我是谁|我是什么身份|我的身份|我叫什么|who\s+am\s+i)", re.I)

_CASUAL = re.compile(
    r"^("
    r"你好|您好|hello|\bhi\b|嗨|在吗|早安|午安|晚安|"
    r"谢谢(?:你|啦|了)?|感谢|thanks|thank\s*you|"
    r"哈哈+[^\u4e00-\u9fff]{0,8}|呵呵+[^\u4e00-\u9fff]{0,8}|嘿+|"
    r"(?:哈哈+|呵呵+)?\s*(?:在忙吗|忙吗)|"
    r"好的?(?:明白了?|知道了?)?|好哒|好呀|行|可以|"
    r"收到|明白了?|了解|知道了|嗯嗯|嗯+|哦+|喔+|ok|okay|"
    r"没事|算了|不用了|先这样"
    r")[!！。.?？\s]*$",
    re.I,
)

_CAPABILITY_REFUSE = re.compile(
    r"(改|修改|调整).{0,8}(权限|角色|密码)|(帮我发布|帮我上线|写回|删除周报)",
    re.I,
)

_LIST = re.compile(
    r"(有哪些期|有哪些.{0,10}期次|哪些周报|列出.*期|期次列表|list\s*issues?|有哪几期)",
    re.I,
)
_REL = re.compile(
    r"(关系|对接|双边|交叉|两边|交集|谁见了谁|联动|relations?)",
    re.I,
)
_REL_CONTACT = re.compile(r"(谁接触了谁|两边.*接触|接触.*交集|接触关系)", re.I)
_DRAFT_RAW = re.compile(
    r"(草稿|draft|原文|raw\s*source|未上线|unpublished|sources?\s*表|直接查库)",
    re.I,
)

# bare ambiguous / incomplete
_AMBIGUOUS_BARE = re.compile(
    r"^("
    r"那个|那个项目|那个事|那件事|之前那个|这个|这件事|"
    r"后来呢|后来怎么样|还有吗|还有没有|还有一个|"
    r"最近怎么样|这周有啥|最近怎样|"
    r"你知道这个吗|那个呢"
    r")[?？!！。.\s]*$",
    re.I,
)

# 「X最近怎么样」— 有主体但仍偏模糊 → 澄清
_HOW_IS = re.compile(r"^(.{1,16}?)最近怎么样[?？!！。.\s]*$", re.I)

# follow-up shapes
_FU_THAT_ENTITY = re.compile(
    r"^那\s*(?P<name>[\u4e00-\u9fffA-Za-z0-9·．\.]{1,20}?)\s*(?:呢|呢\？|\？|\?|怎么样|如何|后来)?[?？!！。.\s]*$",
    re.I,
)
_FU_HE = re.compile(
    r"^(他|她|它|对方)(?:呢|后来|还有吗|怎么样|如何|跟谁\S*|对接\S*|相关\S*)?[?？!！。.\s]*$",
    re.I,
)
_FU_LATER = re.compile(r"^(后来呢|后来怎么样|之后呢|然后呢)[?？!！。.\s]*$", re.I)
_FU_MORE = re.compile(r"^(还有吗|还有没有|还有别的吗|还有其他吗)[?？!！。.\s]*$", re.I)
_FU_THIS_PROJECT = re.compile(r"^(这个项目|那个项目)\s*(?:呢|怎么样|如何)?[?？!！。.\s]*$", re.I)
_FU_WHICH_ISSUE = re.compile(r"^(那个|这个)?是哪一期|依据呢|依据在哪|哪一期", re.I)

_STOP = frozenset(
    {
        "最近", "怎么样", "如何", "什么", "哪些", "这个", "那个", "还有", "后来",
        "本期", "这一期", "编辑部", "商务", "海外", "公司", "项目", "事情",
        "谢谢", "你好", "帮助", "关系", "接触", "沟通", "对接",
    }
)

_BOLD_ENT = re.compile(r"\*\*([^*]{1,24})\*\*")
_CN_NAME = re.compile(r"[\u4e00-\u9fff]{2,6}")


@dataclass
class RouteDecision:
    route: str  # meta|casual|clarify|followup|list|relations|ask|refuse
    intent: str  # maps to runtime intent
    rewritten_query: str = ""
    clarify_text: str = ""
    casual_text: str = ""
    topic_frame: str = ""
    resolved_entity: str = ""
    notes: str = ""


def normalize_query(text: str) -> str:
    q = (text or "").strip()
    q = re.sub(r"@_user_\d+", " ", q)
    q = re.sub(r"@[^\s@]+", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    q = re.sub(r"(?i)^(?:geekpark\s+)?mesh\s+", "", q).strip()
    return q


def _topic_frame_from_query(q: str, intent_hint: str = "") -> str:
    if intent_hint == "ask_relations" or _REL.search(q) or _REL_CONTACT.search(q):
        return "relations"
    if re.search(r"(接触|聊过|沟通过|对接|见了)", q):
        return "contact"
    if re.search(r"(进度|进展|怎么样|动作|在忙)", q):
        return "progress"
    return "about"


def _rewrite_for_entity(entity: str, frame: str, last_query: str = "") -> str:
    e = (entity or "").strip()
    if not e:
        return ""
    if frame == "relations":
        return f"{e}相关有哪些可同步的关系或对接记录"
    if frame == "contact":
        return f"{e}最近跟谁聊过或有过接触"
    if frame == "progress":
        return f"{e}最近有什么进展或动作"
    # inherit last query shape if useful
    if last_query and any(x in last_query for x in ("接触", "聊", "关系")):
        if "关系" in last_query:
            return f"{e}相关有哪些可同步的关系或对接记录"
        return f"{e}最近跟谁聊过或有过接触"
    return f"{e}在已上线周报里有哪些相关记录"


def extract_entities_from_text(text: str, *, limit: int = 12) -> list[str]:
    """从用户问句/回答里抽实体名（仅作指代，不作事实）。"""
    out: list[str] = []
    seen: set[str] = set()

    def add(name: str) -> None:
        n = (name or "").strip().strip("：:，,。.;；、")
        if not n or n in _STOP or len(n) < 2 or len(n) > 20:
            return
        if n in seen:
            return
        seen.add(n)
        out.append(n)

    for m in _BOLD_ENT.findall(text or ""):
        add(m)
    # 「小鹏：」「· 高德」行首
    for m in re.finditer(r"(?:^|\n)\s*[-•·]?\s*\*?\*?([\u4e00-\u9fffA-Za-z0-9]{2,12})\*?\*?[：:]", text or ""):
        add(m.group(1))
    # query 里的专名：去掉功能词后剩余片段
    q = normalize_query(text)
    for token in re.split(r"[，,。.\s、？?！!：:；;/\-]+", q):
        if _CN_NAME.fullmatch(token) or re.fullmatch(r"[A-Za-z][A-Za-z0-9\-_]{1,20}", token or ""):
            add(token)
    return out[:limit]


def merge_entities(prior: list[str], new: list[str], *, limit: int = 16) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for n in list(new) + list(prior):
        if n and n not in seen:
            seen.add(n)
            out.append(n)
        if len(out) >= limit:
            break
    return out


def route_message(
    text: str,
    state: SessionContextState | None = None,
) -> RouteDecision:
    """入口路由。Follow-up 产出 rewritten_query；不读答案当事实。"""
    st = state or SessionContextState()
    q = normalize_query(text)
    if not q:
        return RouteDecision(
            route="refuse",
            intent="refuse",
            notes="empty",
        )

    # draft/raw → refuse
    if _DRAFT_RAW.search(q) and not _META.search(q):
        if re.search(r"(看|读|查|打开|给我|导出).*(草稿|draft|原文|raw|未上线)", q, re.I) or \
           re.search(r"(草稿|draft|原文|raw|未上线).*(内容|全文|json)", q, re.I) or \
           re.search(r"直接查库|查\s*sources", q, re.I):
            return RouteDecision(route="refuse", intent="refuse", notes="draft_raw")

    if _META.search(q) or _META_SHORT.match(q):
        return RouteDecision(route="meta", intent="help", notes="meta")

    if _WHOAMI.search(q):
        return RouteDecision(route="meta", intent="whoami", notes="whoami")

    if _CASUAL.match(q):
        return RouteDecision(
            route="casual",
            intent="casual",
            casual_text=_casual_reply(q),
            notes="casual",
        )

    if _CAPABILITY_REFUSE.search(q):
        return RouteDecision(
            route="refuse",
            intent="refuse",
            notes="capability_boundary",
        )

    # Follow-up resolution (needs session entities OR named entity in utterance)
    fu = _try_followup(q, st)
    if fu is not None:
        return fu

    # Ambiguous bare
    if _AMBIGUOUS_BARE.match(q):
        return RouteDecision(
            route="clarify",
            intent="clarify",
            clarify_text=_clarify_bare(q, st),
            notes="ambiguous_bare",
        )

    m_how = _HOW_IS.match(q)
    if m_how:
        who = m_how.group(1).strip()
        if who and who not in _STOP:
            return RouteDecision(
                route="clarify",
                intent="clarify",
                clarify_text=(
                    f"你想了解「{who}」最近的沟通对象，还是最近负责/推进的事情？"
                    "也可以直接说，比如：「张三最近跟谁聊过？」或「张三这周有啥动作？」"
                ),
                notes="how_is_clarify",
                resolved_entity=who,
            )

    if _LIST.search(q):
        return RouteDecision(route="list", intent="list_issues", notes="list")

    if _REL.search(q) or _REL_CONTACT.search(q):
        return RouteDecision(
            route="relations",
            intent="ask_relations",
            rewritten_query=q,
            topic_frame="relations",
            notes="relations",
        )

    return RouteDecision(
        route="ask",
        intent="ask_published",
        rewritten_query=q,
        topic_frame=_topic_frame_from_query(q),
        notes="business",
    )


def _try_followup(q: str, st: SessionContextState) -> RouteDecision | None:
    frame = st.last_topic_frame or "about"
    primary = (st.active_entities[0] if st.active_entities else "") or ""

    if _FU_THIS_PROJECT.match(q):
        if not primary:
            return RouteDecision(
                route="clarify",
                intent="clarify",
                clarify_text="你指的是哪个项目？可以说项目名或公司名。",
                notes="followup_project_no_ctx",
            )
        return RouteDecision(
            route="followup",
            intent="ask_published",
            rewritten_query=f"{primary}项目最近进展如何",
            topic_frame="progress",
            resolved_entity=primary,
            notes="followup_project",
        )

    m = _FU_THAT_ENTITY.match(q)
    if m:
        name = (m.group("name") or "").strip()
        if name in ("个", "些", "么", "样", "边", "里") or len(name) < 2:
            return None
        if name.startswith("个") or name in ("个项目", "件事", "个事"):
            return None
        if name in ("商务", "编辑部", "海外", "投资"):
            # team switch follow-up
            return RouteDecision(
                route="followup",
                intent="ask_published" if frame != "relations" else "ask_relations",
                rewritten_query=_rewrite_for_entity(name, frame, st.last_query)
                if frame != "about"
                else f"{name}关注了哪些公司或接触对象",
                topic_frame=frame or "about",
                resolved_entity=name,
                notes="followup_team_or_entity",
            )
        rw = _rewrite_for_entity(name, frame, st.last_query)
        intent = "ask_relations" if frame == "relations" else "ask_published"
        return RouteDecision(
            route="followup",
            intent=intent,
            rewritten_query=rw,
            topic_frame=frame,
            resolved_entity=name,
            notes="followup_named",
        )

    if _FU_HE.match(q):
        if not primary:
            return RouteDecision(
                route="clarify",
                intent="clarify",
                clarify_text="你说的「他/她」是指哪一位？可以说名字，我按已上线周报再查。",
                notes="followup_he_no_entity",
            )
        intent = "ask_relations" if frame == "relations" else "ask_published"
        return RouteDecision(
            route="followup",
            intent=intent,
            rewritten_query=_rewrite_for_entity(primary, frame, st.last_query),
            topic_frame=frame,
            resolved_entity=primary,
            notes="followup_pronoun",
        )

    if _FU_LATER.match(q) or _FU_MORE.match(q):
        if not primary and not st.active_entities:
            return RouteDecision(
                route="clarify",
                intent="clarify",
                clarify_text="你想接着问哪个人或哪家公司？可以说名字，或先问一句完整的，比如「小鹏后来怎么样了」。",
                notes="followup_later_no_ctx",
            )
        ent = primary or st.active_entities[0]
        if _FU_MORE.match(q):
            rw = f"{ent}还有其他相关记录或接触对象吗"
        else:
            rw = f"{ent}后来还有后续进展吗"
        intent = "ask_relations" if frame == "relations" else "ask_published"
        return RouteDecision(
            route="followup",
            intent=intent,
            rewritten_query=rw,
            topic_frame=frame,
            resolved_entity=ent,
            notes="followup_later_or_more",
        )

    if _FU_WHICH_ISSUE.search(q) and len(q) <= 20:
        if st.active_issue:
            return RouteDecision(
                route="casual",
                intent="casual",
                casual_text=f"上面说的内容对应期次是 **{st.active_issue}**。还想顺着问谁或哪家公司，直接说就行。",
                notes="followup_which_issue",
            )
        return RouteDecision(
            route="clarify",
            intent="clarify",
            clarify_text="你想确认哪一条结论的期次？可以先点名人或公司，我按已上线周报标出期次。",
            notes="followup_issue_no_ctx",
        )

    return None


def _casual_reply(q: str) -> str:
    ql = q.lower()
    if re.search(r"谢谢|感谢|thanks", q, re.I):
        return "不客气。还想继续问谁、哪家公司，或「那××呢」，直接说就行。"
    if re.search(r"好的|收到|明白|了解|知道了|ok|行|可以", q, re.I):
        return "好。需要的话可以接着问。"
    if re.search(r"算了|不用了|没事|先这样", q):
        return "好，需要再问随时叫我。"
    if re.search(r"哈哈|呵呵", q):
        return "🙂 有事直接问就行。"
    if re.search(r"你好|您好|hello|\bhi\b|嗨|在吗", q, re.I):
        return (
            "你好，我是 Mesh，可以帮你查已上线周报里的人和事。"
            "比如：「张三最近跟谁聊过？」「这个项目是谁在跟？」"
        )
    return "嗯，我在。有关于已上线周报的问题可以直接问。"


def _clarify_bare(q: str, st: SessionContextState) -> str:
    if st.active_entities:
        hint = "、".join(st.active_entities[:3])
        return (
            f"你想接着问哪一个？刚才提到过：{hint}。"
            "也可以说「那××呢」「后来呢」，或换成完整一句。"
        )
    if "最近怎么样" in q or "这周有啥" in q:
        return (
            "范围有点宽。你想了解某个人/公司的沟通对象，还是最近在推进的事？"
            "可以说名字，例如：「小鹏最近有接触吗」。"
        )
    return (
        "我还不太确定你指的是哪个人、哪家公司或哪件事。"
        "可以说具体一点，例如：「张三最近跟谁聊过？」「小鹏和编辑部有什么关系？」"
    )


def update_state_after_turn(
    state: SessionContextState,
    *,
    route: RouteDecision,
    user_text: str,
    answer_text: str = "",
    intent: str = "",
    issue: str = "",
    evidence_refs: list[str] | None = None,
    team: str = "",
) -> SessionContextState:
    """写入会话状态。answer 只用于抽实体名，不作事实缓存。"""
    state.turn_id = int(state.turn_id or 0) + 1
    state.last_route = route.route
    state.last_intent = intent or route.intent
    state.last_query = normalize_query(user_text)
    if route.rewritten_query:
        state.last_query = route.rewritten_query
    q_ents = extract_entities_from_text(user_text)
    if route.resolved_entity:
        q_ents = merge_entities([route.resolved_entity], q_ents)
    a_ents = extract_entities_from_text(answer_text) if answer_text else []
    # 回答抽实体仅作指代候选；限制数量避免噪声
    state.active_entities = merge_entities(state.active_entities, merge_entities(q_ents, a_ents[:8]))
    state.last_query_refs = q_ents[:8]
    if evidence_refs is not None:
        state.last_evidence_refs = list(evidence_refs)[:16]
    if issue:
        state.active_issue = issue
        state.active_period = issue
    if team:
        state.active_team = team
    if route.topic_frame:
        state.last_topic_frame = route.topic_frame
    elif intent == "ask_relations":
        state.last_topic_frame = "relations"
    elif not state.last_topic_frame:
        state.last_topic_frame = _topic_frame_from_query(state.last_query, intent)
    if route.route == "clarify":
        state.unresolved_references = [user_text.strip()]
    else:
        state.unresolved_references = []
    return state


def intent_to_tool(intent: str) -> str | None:
    return {
        "help": "system.help",
        "whoami": None,
        "casual": None,
        "clarify": None,
        "list_issues": "context.list_issues",
        "ask_relations": "ask.relations_summary",
        "ask_published": "ask.published",
        "refuse": None,
    }.get(intent)
