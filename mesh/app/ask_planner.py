"""Planner-lite：纯规则检索规划（无 LLM / ReAct）。

输出 RetrievalPlan，供 ask_engine.prepare 选择 structured 或 hybrid。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from . import qa_structured

CONFIDENCE_THRESHOLD = 0.7

Path = Literal["structured", "hybrid"]
SetOp = Literal["diff", "intersect", "by_team", "overseas_gap", "none"]

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


def _intent_to_plan(intent: dict, confidence: float) -> RetrievalPlan:
    t = intent.get("type") or "none"
    set_op: SetOp = t if t in ("diff", "intersect", "by_team", "overseas_gap") else "none"
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
    """纯规则规划：原始问句 → structured/hybrid + set_op + confidence。"""
    q = (q or "").strip()
    if not q:
        return RetrievalPlan()

    if _skip_structured_for_context(q, refs):
        return RetrievalPlan(path="hybrid", set_op="none", confidence=0.0)

    win = _win(q)

    for fn in (_plan_overseas_gap, _plan_by_team, _plan_diff, _plan_intersect):
        hit = fn(q, win)
        if hit:
            intent, conf = hit
            return _intent_to_plan(intent, conf)

    # 兼容已有 parse_intent 覆盖（e01/e09 等）
    legacy = qa_structured.parse_intent(q)
    if legacy and legacy.get("type") != "error":
        t = legacy["type"]
        conf = 0.88 if t in ("diff", "intersect", "overseas_gap") else 0.86
        return _intent_to_plan(legacy, conf)

    return RetrievalPlan(path="hybrid", set_op="none", confidence=0.0)
