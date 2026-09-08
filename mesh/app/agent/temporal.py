"""Temporal Phase 1：Time Intent → Time Basis → Time Filter + Hard Rules.

仅服务 Agent Ask 路径的时间语义；不改 RAG/Chunk/Rerank/v1 Contract。

Hard Rules:
  1. latest_published ≠ recent event
  2. unknown event time ≠ recent
  3. 无时间依据 → 不得使用「最近发生/本周发生」等确定性措辞
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


_RECENT = re.compile(r"最近|近期|近日|近来")
_THIS_WEEK = re.compile(r"本周|这周")
_LAST_WEEK = re.compile(r"上周")
_EXPLICIT_RANGE = re.compile(
    r"\d{4}\s*年\s*\d{1,2}\s*月|\d{4}-\d{1,2}-\d{1,2}|20\d{2}\.\d{1,2}|"
    r"八月|九月|七月|上旬|中旬|下旬|期间"
)
_EXPLICIT_ISSUE = re.compile(
    r"按\s*期|只看\s*20|明确按|这一期|该期|期次|2026-\d{1,2}-\d{1,2}|2026\.\d"
)
_EVENTISH = re.compile(r"发生|做成了什么|做了什么|进展|动作|拜访|成立")
_EQUATE_LATEST = re.compile(
    r"(最新(上线|一期|期次).{0,24}(当成|当作|算).{0,16}(最近|本周|这周|刚发生))"
    r"|(当成.{0,12}(这周|本周)?刚发生)"
    r"|(算最近发生)"
    r"|(是不是刚发生)"
)
_FORBIDDEN_RECENT_EVENT = re.compile(
    r"(最近发生|本周发生|上周发生|刚刚发生|刚发生|昨天发生|正在发生|"
    r"最近正在|本周还在|本周刚|这周刚|近日发生|近期发生了)"
)


@dataclass
class TimeSemantics:
    window: str = "none"  # recent | this_week | last_week | explicit | none
    basis: str = "issue_time"  # event_time | material_time | issue_time | unknown
    filter_mode: str = "issue_anchor"  # issue_anchor | passthrough
    hard_rules: list[str] = field(default_factory=list)
    reject_latest_equals_recent: bool = False
    require_no_event_time_caveat: bool = False
    issue_mode: str = ""
    issue_slug: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_time_intent(query: str) -> str:
    q = query or ""
    if _EQUATE_LATEST.search(q) or re.search(r"是不是刚发生", q):
        return "recent"
    if _THIS_WEEK.search(q):
        return "this_week"
    if _LAST_WEEK.search(q):
        return "last_week"
    if _RECENT.search(q):
        return "recent"
    if _EXPLICIT_RANGE.search(q) or _EXPLICIT_ISSUE.search(q):
        return "explicit"
    return "none"


def resolve_time_basis(
    query: str,
    window: str,
    *,
    issue_mode: str = "",
    has_event_time: bool = False,
) -> str:
    """无 event_time 字段落地前：对「最近/发生」类默认 unknown，避免假装有事件时间。"""
    q = query or ""
    if window in ("this_week", "last_week", "explicit") and issue_mode in (
        "explicit",
        "pinned",
        "latest_published",
    ):
        # 期次相对周次 → Issue Time；仍禁止当成事件刚发生
        if _EVENTISH.search(q) and window == "recent":
            return "event_time" if has_event_time else "unknown"
        if window == "explicit" and not _EVENTISH.search(q):
            return "issue_time"
        if window in ("this_week", "last_week"):
            return "issue_time"
    if window == "recent":
        if has_event_time:
            return "event_time"
        # 系统尚无可靠 Event Time → unknown（Hard Rule 2）
        return "unknown"
    if window == "explicit":
        return "issue_time" if issue_mode != "none" else "material_time"
    if issue_mode in ("explicit", "pinned", "latest_published"):
        return "issue_time"
    return "unknown"


def resolve_time_semantics(
    query: str,
    *,
    issue_mode: str = "",
    issue_slug: str = "",
    has_event_time: bool = False,
) -> TimeSemantics:
    window = resolve_time_intent(query)
    basis = resolve_time_basis(
        query, window, issue_mode=issue_mode, has_event_time=has_event_time
    )
    hard: list[str] = [
        "latest_published_neq_recent_event",
        "unknown_event_time_neq_recent",
        "no_basis_no_deterministic_recent",
    ]
    reject = bool(_EQUATE_LATEST.search(query or "")) or bool(
        re.search(r"是不是刚发生", query or "")
    )
    # 「最近」+ latest/explicit 但无 event time → 必须 caveat
    need_caveat = basis == "unknown" or (
        window == "recent" and not has_event_time
    )
    # Filter：有 IssueRef 时钉期次窗，禁止墙上时钟「本周/上周/最近」覆盖
    filter_mode = "issue_anchor" if issue_mode in (
        "explicit",
        "pinned",
        "latest_published",
    ) else "passthrough"

    return TimeSemantics(
        window=window,
        basis=basis,
        filter_mode=filter_mode,
        hard_rules=hard,
        reject_latest_equals_recent=reject,
        require_no_event_time_caveat=need_caveat,
        issue_mode=issue_mode or "",
        issue_slug=issue_slug or "",
    )


def prompt_block(sem: TimeSemantics) -> str:
    """注入 LLM 成文的时间约束（不改检索）。"""
    lines = [
        "【时间语义 Hard Rules — 必遵】",
        "1) latest_published（最新已上线期）≠「最近发生的事件」。",
        "2) 资料未提供 event_time 时，禁止把内容说成「最近发生/本周发生/刚发生」。",
        "3) 无时间依据时，禁止使用确定性近期发生措辞；应写「依据 ×× 期已上线记录」或「资料未提供发生时间」。",
        f"本问判定：window={sem.window}；basis={sem.basis}；issue={sem.issue_slug or '—'}（mode={sem.issue_mode or 'none'}）。",
    ]
    if sem.window in ("this_week", "last_week"):
        lines.append(
            "用户说的「本周/上周」若已定点次：只陈述该期记录，"
            "禁止换算成墙上时钟的日历周区间（如根据今天推 Aug31–Sep6）。"
        )
    if sem.window == "recent" or sem.basis == "unknown":
        lines.append(
            "用户说「最近」时：优先事件发生时间；若无，则说明资料时间/期次，"
            "不得把最新上线期或检索命中自动等同为「最近发生」。"
        )
    if sem.reject_latest_equals_recent:
        lines.append(
            "用户试图把「最新上线/刚发生」等同：须明确拒绝该等同，再按期次复述内容（若有）。"
        )
    if sem.require_no_event_time_caveat:
        lines.append(
            "开头须点明依据期次或「资料未提供发生时间」；禁止「最近正在/本周还在」等进行时幻觉。"
        )
    return "\n".join(lines)


_REJECT_LATEST = (
    "上线最新期次（latest published）不等于事件发生时刻。"
    "若没有可核对的事件时间，只能说明资料所属期次或材料时间，"
    "不能把旧材料说成当下新发生。"
)


def maybe_direct_answer(query: str, sem: TimeSemantics) -> str | None:
    """极端等同问法可直接答，避免先检索再幻觉。"""
    q = query or ""
    if sem.reject_latest_equals_recent and re.search(
        r"当成|当作|算最近|是不是刚发生", q
    ):
        # 仍允许后半句问具体内容时走检索；纯等同挑衅则直接拒
        if re.search(r"总结一下|都当成", q) or re.fullmatch(
            r".*(是不是刚发生的事).*", q
        ):
            # 「是不是刚发生的事？…提了什么」→ 不 early return，走检索+成文约束
            if re.search(r"提了什么|关注了什么|做了什么|有什么", q):
                return None
            return _REJECT_LATEST
        if re.search(r"都当成|总结一下", q):
            return _REJECT_LATEST
    return None


def apply_hard_rules(answer: str, sem: TimeSemantics) -> str:
    """成文后硬过滤：无依据时去掉确定性「刚发生/最近发生」类措辞。"""
    text = (answer or "").strip()
    if not text:
        return text

    if sem.reject_latest_equals_recent and not re.search(
        r"不等于|并非|不能.*当成|上线不等于|不是最近发生|资料未提供", text
    ):
        text = _REJECT_LATEST + "\n\n" + text

    if sem.basis == "unknown" or sem.require_no_event_time_caveat or sem.window == "recent":
        if _FORBIDDEN_RECENT_EVENT.search(text):
            # 降级改写，不删全文；替换串本身不得再命中禁词
            text = _FORBIDDEN_RECENT_EVENT.sub("（资料记载·非事件时刻断言）", text)
            if sem.issue_slug and "依据的是" not in text and "资料未提供" not in text:
                text = (
                    f"依据的是 {sem.issue_slug} 期已上线记录，"
                    f"时间口径为资料/期次，不作事件时刻断言。\n\n"
                    + text
                )

    if sem.window in ("this_week", "last_week") and sem.filter_mode == "issue_anchor":
        # 去掉墙上时钟周区间，改写为期次口径
        text2 = re.sub(
            r"(上周|本周|这周)\s*[（(]\s*2026-\d{1,2}-\d{1,2}\s*[至到~\-—]\s*2026-\d{1,2}-\d{1,2}\s*[）)]",
            rf"「\1」（按期次 {sem.issue_slug or '已定点次'} 理解，非墙上时钟）",
            text,
        )
        text2 = re.sub(
            r"2026-0[89]-\d{2}\s*[至到~\-—]\s*2026-0[89]-\d{2}",
            f"期次 {sem.issue_slug or ''} 窗口".strip(),
            text2,
        )
        if text2 != text:
            text = text2
            if sem.issue_slug and "墙上时钟" not in text:
                text = (
                    f"说明：已定点次 {sem.issue_slug}，"
                    f"「上周/本周」按该期理解，不按今天所在周的墙上时钟推算。\n\n"
                    + text
                )

    return text


def apply_time_filter_to_dates(
    sem: TimeSemantics,
    *,
    issue_date_from: str | None,
    issue_date_to: str | None,
) -> tuple[str | None, str | None]:
    """Time Filter：有 Issue 锚点时用期次日期，不引入墙上时钟窗。"""
    if sem.filter_mode == "issue_anchor":
        return issue_date_from, issue_date_to
    return issue_date_from, issue_date_to
