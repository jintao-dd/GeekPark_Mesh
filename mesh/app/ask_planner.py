"""Planner-lite：检索规划（LLM 语义意图 + 规则兜底）。

输出 RetrievalPlan，供 ask_engine.prepare 选择 structured 或 hybrid。

意图判断优先用 LLM（语义理解，适配千变万化问法），失败/关闭时回落纯正则。
不管走哪条路，最终「算」的部分仍由确定性 SQL（qa_structured.run_structured）完成，
LLM 只填「要不要做集合运算 / 哪两个队 / 交集还是差集」这张固定表，不参与计算。
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Literal

from . import qa_structured

log = logging.getLogger("uvicorn.error")

CONFIDENCE_THRESHOLD = 0.7

Path = Literal["structured", "hybrid"]
SetOp = Literal["diff", "intersect", "by_team", "overseas_gap", "cooccur", "bridge", "none"]

_POS_DIFF = r"(接触过|跟进过|跟进了|在跟进|跟进中|采访过|关注过|有记录)"
_NEG_DIFF = r"(还没|没有|尚未|没接触|未接触|未跟进)"


@dataclass
class RetrievalPlan:
    path: Path = "hybrid"
    set_op: SetOp = "none"
    section: str = "接触"
    hardware: bool = False
    teams: list[str] = field(default_factory=list)
    confidence: float = 0.0
    intent: dict | None = None
    fallback_note: str = ""


def hybrid_fallback_note(plan: RetrievalPlan) -> str:
    labels = {
        "diff": "差集",
        "intersect": "交集",
        "by_team": "按团队聚合",
        "overseas_gap": "海外与国内缺口",
        "cooccur": "二跳共现",
        "bridge": "二跳桥接",
    }
    op = labels.get(plan.set_op, plan.set_op)
    return (
        f"（说明：问句疑似涉及集合运算「{op}」，但未达到结构化路由置信度，"
        f"以下结果为全文检索，未做集合运算。）"
    )


def _skip_structured_for_context(q: str, refs: dict | None) -> bool:
    """追问 / 带种子 refs 时不走 Planner structured，避免破坏 follow-up。"""
    refs = refs or {}
    if refs.get("chunk_ids") or refs.get("item_ids"):
        return True
    if refs.get("parent_analysis_id"):
        return True
    has_prior = bool(
        (refs.get("last_user_q") or "").strip()
        or refs.get("entities")
        or refs.get("teams")
    )
    if not has_prior:
        return False
    from .ask_context import is_followup

    follow, _ = is_followup(q, has_prior=True)
    return follow


def _win(q: str) -> dict:
    date_from, date_to, window_days = qa_structured.parse_window(q)
    return {"date_from": date_from, "date_to": date_to, "window_days": window_days}


def _plan_diff(q: str, win: dict) -> tuple[dict, float] | None:
    teams = qa_structured.find_teams_in_question(q)
    if len(teams) < 2:
        return None
    section = qa_structured._infer_section(q)
    hardware = bool(re.search(r"硬件|" + "|".join(map(re.escape, qa_structured.HARDWARE_HINTS)), q))

    classic = bool(
        re.search(rf"{_POS_DIFF}.{{0,40}}但.{{0,16}}{_NEG_DIFF}", q)
        or (re.search(rf"但.{{0,12}}{_NEG_DIFF}", q) and re.search(_POS_DIFF, q))
    )
    progressive = bool(re.search(r"跟进了|在跟进|跟进中", q) and re.search(_NEG_DIFF, q))

    if not classic and not progressive:
        return None

    ordered = qa_structured._diff_team_order(q, teams)
    if not ordered:
        return None

    team_a, team_b = ordered
    conf = 0.95 if classic else 0.88
    intent = {
        "type": "diff",
        "team_a": team_a,
        "team_b": team_b,
        "section": section if section in ("接触", "关注", "关系", "看法") else "接触",
        "hardware": hardware,
        **win,
    }
    return intent, conf


def _plan_intersect(q: str, win: dict) -> tuple[dict, float] | None:
    teams = qa_structured.find_teams_in_question(q)
    if len(teams) < 2:
        return None

    hit = bool(
        re.search(r"同时也是|同时是|共同|都是|交集|两边都|双方都|重叠|重合", q)
        or (re.search(r"也是", q) and re.search(r"采访对象|客户里|接触对象|跟进的客户", q))
    )
    if not hit:
        return None

    section = qa_structured._infer_section(q)
    hardware = bool(re.search(r"硬件|" + "|".join(map(re.escape, qa_structured.HARDWARE_HINTS)), q))
    conf = 0.96 if re.search(r"重叠|重合|交集|两边都|同时", q) else 0.85
    intent = {
        "type": "intersect",
        "team_a": teams[0],
        "team_b": teams[1],
        "section": section if section in ("接触", "关注", "关系", "看法") else "接触",
        "hardware": hardware,
        **win,
    }
    return intent, conf


def _plan_by_team(q: str, win: dict) -> tuple[dict, float] | None:
    if not re.search(r"各团队|分别知道|分别了解|各自知道|内部各", q):
        return None

    section = qa_structured._infer_section(q)
    hardware = bool(re.search(r"硬件|" + "|".join(map(re.escape, qa_structured.HARDWARE_HINTS)), q))

    # e10：各团队 + 关注 + 硬件（topic 在「各团队」之后）
    if hardware and re.search(r"关注|话题|选题", q):
        return {
            "type": "by_team",
            "topic": "硬件",
            "hardware": True,
            "section": section,
            **win,
        }, 0.93

    topic = ""
    m = re.search(r"(?:关于|针对)?\s*[「\"']?(.+?)[」\"']?\s*[，,]?\s*(?:公司内部)?各团队", q)
    if m:
        topic = m.group(1).strip(" ，,。的")
    if not topic:
        m = re.search(r"关于\s*(.+?)(?:，|,|公司|内部|各团队|分别)", q)
        if m:
            topic = m.group(1).strip()
    if not topic:
        m = re.search(
            r"(?:分别知道|分别了解|各自知道|各团队).{0,20}?(?:关于|针对)\s*[「\"']?(.+?)[」\"']?\s*$",
            q,
        )
        if m:
            topic = m.group(1).strip(" ，,。的？?")
    # 「各团队…关注了哪些 X 话题/内容」
    if not topic:
        m = re.search(
            r"各团队.{0,24}(?:关注|了解|知道).{0,16}(?:哪些|什么).{0,12}[「\"']?(.+?)[」\"']?\s*(?:相关)?(?:话题|内容|方向)",
            q,
        )
        if m:
            topic = m.group(1).strip(" ，,。的？?")

    topic = (topic or "").strip()
    bad = qa_structured._BAD_BY_TEAM_TOPICS
    if not topic or len(topic) < 2 or topic in bad or re.fullmatch(r"什么.*", topic):
        return None

    return {
        "type": "by_team",
        "topic": topic,
        "hardware": hardware,
        "section": section,
        **win,
    }, 0.9 if len(topic) >= 3 else 0.65


def _plan_overseas_gap(q: str, win: dict) -> tuple[dict, float] | None:
    if not (
        re.search(r"海外", q)
        and re.search(r"国内", q)
        and re.search(r"没有|还没|尚未|没人|无部门", q)
    ):
        return None
    section = qa_structured._infer_section(q)
    hardware = bool(re.search(r"硬件|" + "|".join(map(re.escape, qa_structured.HARDWARE_HINTS)), q))
    return {
        "type": "overseas_gap",
        "section": section if section in ("接触", "关注") else "接触",
        "hardware": hardware,
        **win,
    }, 0.95


# ---- 2 跳规则兜底（LLM 关闭/失败时）----

_COOCCUR_RE = re.compile(
    r"(还(接触过|认识|见过|聊过|跟过|碰过)谁|都(和|跟)谁|一起(出现|提到)|"
    r"(还|另外)有(哪些|谁)|围绕.{1,20}(还有|还涉及)|关联到(哪些|谁)|"
    r"和.{1,16}(一起|同时).{0,8}(出现|提到|相关))"
)
_BRIDGE_RE = re.compile(
    r"(谁(把|将).{1,20}(和|与|、).{1,20}(连|串|连接|联系)起来|"
    r"(共同|都).{0,4}(联系|认识|接触|关联)的(人|公司|主体)|"
    r"同时(关联|连接|涉及).{1,20}(和|与))"
)


_SEED_STOP_RE = re.compile(r"(还有|有哪些|以及|相关|等|都|谁|哪些|什么|的人|的公司|的事|$)")


def _strip_seed(q: str) -> str:
    """从问句里抽出种子主体：优先引号内专名，否则取问句前段专名。"""
    m = re.search(r"[「\"']([^」\"']{2,30})[」\"']", q)
    if m:
        return m.group(1).strip()
    # 「跟/和 X 聊过的人还…」：取「跟/和」与「聊过/接触/跟过」之间
    m = re.search(r"[跟和与]\s*([^\s，,。？?]{2,30}?)\s*(?:聊过|接触|跟过|碰过|有关|相关)", q)
    if m:
        return m.group(1).strip()
    m = re.search(r"(?:围绕|关于|针对)\s*([^\s，,。？?]{2,30})", q)
    if m:
        # 在停止词处截断，避免把「还有哪些相关的人和公司」一起吃进来
        return _SEED_STOP_RE.split(m.group(1).strip())[0].strip(" ，,。的")
    return ""


def _plan_cooccur(q: str, win: dict) -> tuple[dict, float] | None:
    if not _COOCCUR_RE.search(q):
        return None
    seed = _strip_seed(q)
    if len(seed) < 2:
        return None
    return {"type": "cooccur", "seed": seed, **win}, 0.85


def _plan_bridge(q: str, win: dict) -> tuple[dict, float] | None:
    if not _BRIDGE_RE.search(q):
        return None
    names = re.findall(r"[「\"']([^」\"']{2,30})[」\"']", q)
    if len(names) < 2:
        # 退而求其次：抓「把 A 和 B 连起来」的两个专名
        m = re.search(r"把\s*([^\s，,。？?]{2,30}?)\s*(?:和|与|、)\s*([^\s，,。？?]{2,30}?)\s*(?:连|串)", q)
        if m:
            names = [m.group(1), m.group(2)]
    if len(names) < 2 or names[0] == names[1]:
        return None
    return {"type": "bridge", "seed": names[0], "seed_b": names[1], **win}, 0.85


def _intent_to_plan(intent: dict, confidence: float) -> RetrievalPlan:
    t = intent.get("type") or "none"
    set_op: SetOp = t if t in ("diff", "intersect", "by_team", "overseas_gap", "cooccur", "bridge") else "none"
    teams = []
    if intent.get("team_a"):
        teams.append(intent["team_a"])
    if intent.get("team_b"):
        teams.append(intent["team_b"])
    return RetrievalPlan(
        path="structured" if confidence >= CONFIDENCE_THRESHOLD else "hybrid",
        set_op=set_op,
        section=intent.get("section") or "接触",
        hardware=bool(intent.get("hardware")),
        teams=teams,
        confidence=confidence,
        intent=intent,
        fallback_note=hybrid_fallback_note(RetrievalPlan(set_op=set_op)) if set_op != "none" and confidence < CONFIDENCE_THRESHOLD else "",
    )


def plan_retrieval(q: str, refs: dict | None = None) -> RetrievalPlan:
    """问句 → structured/hybrid + set_op + confidence。

    顺序：LLM 语义意图（优先）→ 规则模式 → legacy parse_intent → 纯 hybrid。
    LLM 结果必须通过校验（type 合法 + 队名可归一）才采用，否则回落规则。
    """
    q = (q or "").strip()
    if not q:
        return RetrievalPlan()

    if _skip_structured_for_context(q, refs):
        return RetrievalPlan(path="hybrid", set_op="none", confidence=0.0)

    win = _win(q)

    # 1) LLM 语义意图（可用则优先；校验通过才采用）
    llm_plan = _llm_intent_plan(q, win)
    if llm_plan is not None:
        return llm_plan

    # 2) 规则模式（无 LLM / LLM 失败时的确定性兜底）
    for fn in (_plan_overseas_gap, _plan_by_team, _plan_diff, _plan_intersect,
               _plan_bridge, _plan_cooccur):
        hit = fn(q, win)
        if hit:
            intent, conf = hit
            return _intent_to_plan(intent, conf)

    # 3) 兼容已有 parse_intent 覆盖（e01/e09 等）
    legacy = qa_structured.parse_intent(q)
    if legacy and legacy.get("type") != "error":
        t = legacy["type"]
        conf = 0.88 if t in ("diff", "intersect", "overseas_gap") else 0.86
        return _intent_to_plan(legacy, conf)

    return RetrievalPlan(path="hybrid", set_op="none", confidence=0.0)


def _llm_intent_enabled() -> bool:
    """默认开启；MESH_ASK_LLM_INTENT=0 可回退纯规则。"""
    v = (os.environ.get("MESH_ASK_LLM_INTENT") or "1").strip().lower()
    return v not in ("0", "false", "off", "no")


_LLM_INTENT_SYSTEM = """你是检索意图分类器。判断用户问句需要哪种「结构化检索」，只输出 JSON，不解释。

结构化类型：
- intersect：两个团队都接触/关注/讨论过的「同一批」公司或人（重合、交集、都碰过、同时在跟）。
- diff：团队 A 接触过、团队 B 没接触过（差集、A 有 B 无、A 跟过但 B 还没）。
- by_team：某个话题/领域，各团队分别知道/关注了什么（按团队聚合同一主题）。
- overseas_gap：海外团队接触过、国内团队还没接触的缺口。
- cooccur：某个主体（公司/人/话题）「还跟谁一起出现」——从它出发找相关的其他主体。
    触发词：还接触过谁、都和谁有关、一起出现过、关联到谁、围绕 X 还有谁。
- bridge：哪个人/公司/话题「同时连接」两个主体——找中间桥梁。
    触发词：谁把 X 和 Y 连起来、X 和 Y 的共同联系人、同时关联 X 和 Y 的是谁。
- none：以上都不是（普通检索、单团队、进展、看法、寒暄等）。

规则：
1) 只有语义上确实要「比较两个团队的名单」才给 intersect/diff；模糊问「关注什么」不是。
2) team_a/team_b 用问句里出现的团队原词（如「编辑部」「商业化团队」「硅谷」），系统会自己归一。
3) diff 里 team_a = 有记录的一方，team_b = 没记录的一方。
4) topic 仅 by_team 用，填被比较的话题词（如「AI 助听器」「硬件」）。
5) cooccur 用 seed 填那个起点主体；bridge 用 seed / seed_b 填两个主体（用问句里的原词）。
6) 拿不准就给 none，宁可漏判也不要误判。

输出：{"type": "...", "team_a": "", "team_b": "", "topic": "", "seed": "", "seed_b": "", "confidence": 0.0-1.0}"""


def _llm_intent_plan(q: str, win: dict) -> RetrievalPlan | None:
    if not _llm_intent_enabled():
        return None
    try:
        from . import llm

        raw = llm.call(
            _LLM_INTENT_SYSTEM,
            f"问句：{q}\n只输出 JSON：",
            max_tokens=200,
            json_mode=True,
            task="semantic",
        )
        data = raw if isinstance(raw, dict) else json.loads(str(raw or "").strip())
    except Exception as e:
        log.info("ask llm-intent failed, fallback rules: %s", e)
        return None

    if not isinstance(data, dict):
        return None
    t = str(data.get("type") or "none").strip().lower()
    if t not in ("diff", "intersect", "by_team", "overseas_gap", "cooccur", "bridge"):
        # LLM 判定普通检索 → 不短路，让规则兜底再决定（避免 LLM 漏掉 legacy 覆盖）
        return None

    conf = data.get("confidence")
    try:
        conf = float(conf)
    except Exception:
        conf = 0.8
    conf = max(0.0, min(1.0, conf))
    section = qa_structured._infer_section(q)
    hardware = bool(
        re.search(r"硬件|" + "|".join(map(re.escape, qa_structured.HARDWARE_HINTS)), q)
    )

    if t == "overseas_gap":
        intent = {
            "type": "overseas_gap",
            "section": section if section in ("接触", "关注") else "接触",
            "hardware": hardware,
            **win,
        }
        return _validate_and_plan(intent, max(conf, 0.9), q)

    if t == "by_team":
        topic = str(data.get("topic") or "").strip(" ，,。的？?")
        if not topic and hardware:
            topic = "硬件"
        if not topic or len(topic) < 2 or topic in qa_structured._BAD_BY_TEAM_TOPICS:
            return None
        intent = {
            "type": "by_team",
            "topic": topic,
            "hardware": hardware,
            "section": section,
            **win,
        }
        return _validate_and_plan(intent, max(conf, 0.85), q)

    # cooccur / bridge：种子主体必须非空（不强制归一，实体名允许自由文本）
    if t == "cooccur":
        seed = str(data.get("seed") or "").strip(" ，,。的？?\"'「」")
        if len(seed) < 2:
            return None
        intent = {"type": "cooccur", "seed": seed, "section": section, **win}
        return _validate_and_plan(intent, max(conf, 0.85), q)

    if t == "bridge":
        seed = str(data.get("seed") or "").strip(" ，,。的？?\"'「」")
        seed_b = str(data.get("seed_b") or "").strip(" ，,。的？?\"'「」")
        if len(seed) < 2 or len(seed_b) < 2 or seed == seed_b:
            return None
        intent = {"type": "bridge", "seed": seed, "seed_b": seed_b, "section": section, **win}
        return _validate_and_plan(intent, max(conf, 0.85), q)

    # diff / intersect：两个队都必须能归一
    ta = qa_structured._normalize_team(str(data.get("team_a") or ""))
    tb = qa_structured._normalize_team(str(data.get("team_b") or ""))
    if not ta or not tb or ta == tb:
        log.info("ask llm-intent %s dropped: teams=%r/%r not resolvable", t, data.get("team_a"), data.get("team_b"))
        return None
    intent = {
        "type": t,
        "team_a": ta,
        "team_b": tb,
        "section": section if section in ("接触", "关注", "关系", "看法") else "接触",
        "hardware": hardware,
        **win,
    }
    return _validate_and_plan(intent, max(conf, 0.85), q)


def _validate_and_plan(intent: dict, conf: float, q: str) -> RetrievalPlan | None:
    """LLM 意图入 structured 前的确定性校验；不通过返回 None 回落规则。"""
    t = intent.get("type")
    if t not in ("diff", "intersect", "by_team", "overseas_gap", "cooccur", "bridge"):
        return None
    plan = _intent_to_plan(intent, conf)
    plan.intent = intent
    log.info(
        "ask llm-intent accepted type=%s conf=%.2f teams=%s topic=%s seed=%s",
        t,
        conf,
        plan.teams,
        intent.get("topic") or "-",
        intent.get("seed") or "-",
    )
    return plan
