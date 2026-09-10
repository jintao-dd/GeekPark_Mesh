"""Feishu Agent v1 · 用户可见回复格式。

核心原则：可验证事实回答必须带 Claim 对齐的 Evidence / 期次引用。
Failure UX：no_hit / insufficient / permission / system_error 分腔，禁止统一 no_evidence。
不改 Retrieval / Ranking / Claim Support 判定本身。
"""
from __future__ import annotations

import re
from typing import Any

from .models import AgentAnswer, ClaimBinding

_NO_HIT_MARKERS = (
    "未在已上线周报中找到与问题直接相关的记录",
    "未在已上线语料中找到可引用依据",
    "我目前没查到已发布的内容能确认这件事",
)

_NO_HIT_UX = "我目前没查到已发布的内容能确认这件事。可以换个关键词，或确认相关内容是否已上线。"
_INSUFFICIENT_UX = (
    "我找到了一些相关内容，但它们还不足以支撑一个更强的结论。"
    "如果你愿意，可以缩小问题（比如点名人/公司，或问「是哪一期」）。"
)
_CONTRADICTED_UX = "已上线记录里存在与该说法不一致的内容，我不能按原说法下结论。"
_SYSTEM_UX = "刚才检索没成功，你可以再试一次。"


def _issue_slug(answer: AgentAnswer, payload: dict[str, Any] | None = None) -> str:
    p = payload or {}
    if p.get("issue"):
        return str(p["issue"]).strip()
    ctx = answer.context or {}
    iref = ctx.get("issue_ref") or {}
    if isinstance(iref, dict) and iref.get("slug"):
        return str(iref["slug"]).strip()
    return ""


def _support_label(answer: AgentAnswer, payload: dict[str, Any] | None = None) -> tuple[str, str]:
    p = payload or {}
    cs = p.get("claim_support") or answer.trace.get("claim_support") or {}
    if isinstance(cs, dict):
        return str(cs.get("support") or "").strip(), str(cs.get("reason") or "").strip()
    for b in answer.claim_bindings or []:
        if b.status == "unsupported":
            return "insufficient", b.reason or ""
        if b.status == "grounded":
            return "supported", b.reason or ""
        if b.status == "weak":
            return "insufficient", b.reason or ""
    return "", ""


def _failure_kind(
    answer: AgentAnswer,
    payload: dict[str, Any] | None,
    support: str,
    reason: str,
) -> str:
    """no_hit | insufficient | contradicted | permission | system_error | ok"""
    if answer.refused or answer.intent == "refuse":
        dr = (answer.deny_reason or "").lower()
        if "acl" in dr or "permission" in dr or "identity" in dr:
            return "permission"
        return "permission" if "无权" in (answer.text or "") else "ok"
    if answer.intent in ("help", "whoami", "casual", "clarify", "list_issues"):
        return "ok"
    body = (answer.text or "").strip()
    if any(m in body for m in _NO_HIT_MARKERS) or reason in ("no_evidence", "no_hit", "empty"):
        # n_hits==0 更像 no_hit
        n_hits = (payload or {}).get("n_hits")
        if n_hits == 0 or support in ("", "insufficient", "no_evidence") or any(
            m in body for m in _NO_HIT_MARKERS
        ):
            if support == "contradicted":
                return "contradicted"
            if n_hits and int(n_hits) > 0 and support == "insufficient":
                return "insufficient"
            if any(m in body for m in _NO_HIT_MARKERS) and (not n_hits or int(n_hits or 0) == 0):
                return "no_hit"
            if support == "insufficient":
                return "insufficient"
            return "no_hit"
    if support == "contradicted":
        return "contradicted"
    if support == "insufficient":
        return "insufficient"
    if "没查成功" in body or "再试一次" in body:
        return "system_error"
    return "ok"


def _humanize_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        return ""
    m = re.search(r"item[:/]?(\d+)", r, re.I)
    if m:
        return f"条目 {m.group(1)}"
    return r


def _rewrite_body_for_failure(body: str, kind: str) -> str:
    if kind == "no_hit":
        # 去掉机械检索提示尾巴，换人话
        if any(m in body for m in _NO_HIT_MARKERS) or len(body) < 80:
            return _NO_HIT_UX
        return body
    if kind == "insufficient":
        if any(m in body for m in _NO_HIT_MARKERS) or "依据不足" in body:
            return _INSUFFICIENT_UX
        # 保留模型已写的谨慎结论，不叠模板
        return body
    if kind == "contradicted":
        if any(m in body for m in _NO_HIT_MARKERS):
            return _CONTRADICTED_UX
        return body
    if kind == "system_error":
        return _SYSTEM_UX
    return body


def format_display_text(
    answer: AgentAnswer,
    *,
    payload: dict[str, Any] | None = None,
) -> str:
    """组装飞书/产品侧可见正文：Answer + 期次 + Claim 对齐证据。"""
    body = (answer.text or "").strip()
    if answer.refused and answer.intent == "refuse":
        return body

    if answer.intent in ("help", "whoami", "casual", "clarify", "list_issues"):
        return body

    issue = _issue_slug(answer, payload)
    support, reason = _support_label(answer, payload)
    kind = _failure_kind(answer, payload, support, reason)
    body = _rewrite_body_for_failure(body, kind)

    refs = list(answer.evidence_refs or [])
    if not refs:
        for b in answer.claim_bindings or []:
            refs.extend(list(b.evidence_refs or []))
    seen: set[str] = set()
    uniq_refs: list[str] = []
    for r in refs:
        if r and r not in seen:
            seen.add(r)
            uniq_refs.append(r)

    blocks: list[str] = [body]
    meta_lines: list[str] = []

    if issue:
        meta_lines.append(f"期次：{issue}")

    # 人话判定，禁止甩 no_evidence 英文腔
    if kind == "no_hit":
        meta_lines.append("依据：暂无直接命中")
    elif kind == "insufficient":
        meta_lines.append("依据：有相关内容，但不足以支撑更强结论")
    elif kind == "contradicted":
        meta_lines.append("依据：存在不一致记录")
    elif support == "supported":
        meta_lines.append("依据：有已上线内容支持")
    elif support:
        meta_lines.append(f"依据：{support}")

    if uniq_refs and kind not in ("no_hit", "system_error"):
        meta_lines.append("可核对：")
        for r in uniq_refs[:8]:
            meta_lines.append(f"· {_humanize_ref(r)}")
    elif kind == "insufficient" and not uniq_refs:
        meta_lines.append("可核对：当前可见内容还不够直接。")

    if meta_lines and kind != "system_error":
        blocks.append("")
        blocks.append("——")
        blocks.extend(meta_lines)

    return "\n".join(blocks).strip()


def enrich_answer_for_display(
    answer: AgentAnswer,
    *,
    payload: dict[str, Any] | None = None,
) -> AgentAnswer:
    """写入 trace.display_text；不改大脑判定。"""
    display = format_display_text(answer, payload=payload)
    answer.trace = dict(answer.trace or {})
    answer.trace["display_text"] = display
    support, reason = _support_label(answer, payload)
    kind = _failure_kind(answer, payload, support, reason)
    answer.trace["failure_kind"] = kind
    if payload and payload.get("claim_support"):
        answer.trace["claim_support"] = payload.get("claim_support")
    if payload and payload.get("issue"):
        answer.trace["issue"] = payload.get("issue")
    return answer


def claim_bindings_summary(bindings: list[ClaimBinding]) -> list[dict[str, Any]]:
    return [b.to_dict() for b in (bindings or [])]
