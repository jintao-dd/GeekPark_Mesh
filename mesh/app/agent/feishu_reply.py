"""Feishu Agent v1 · 用户可见回复格式。

核心原则：可验证事实回答必须带 Claim 对齐的 Evidence / 期次引用。
不改 Retrieval / Ranking / Claim Support；只做展示层组装。
"""
from __future__ import annotations

import re
from typing import Any

from .models import AgentAnswer, ClaimBinding


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
    # fallback: claim_bindings status
    for b in answer.claim_bindings or []:
        if b.status == "unsupported":
            return "insufficient", b.reason or ""
        if b.status == "grounded":
            return "supported", b.reason or ""
        if b.status == "weak":
            return "insufficient", b.reason or ""
    return "", ""


def _humanize_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not r:
        return ""
    # ev:item:4251 | ev:2026-8-17:item:4251 | similar
    m = re.search(r"item[:/]?(\d+)", r, re.I)
    if m:
        return f"条目 {m.group(1)}（{r}）"
    return r


def format_display_text(
    answer: AgentAnswer,
    *,
    payload: dict[str, Any] | None = None,
) -> str:
    """组装飞书/产品侧可见正文：Answer + 期次 + Claim 对齐证据。"""
    body = (answer.text or "").strip()
    if answer.refused and answer.intent == "refuse":
        return body

    # help / list_issues：不强制证据块
    if answer.intent in ("help", "list_issues"):
        return body

    issue = _issue_slug(answer, payload)
    support, reason = _support_label(answer, payload)
    refs = list(answer.evidence_refs or [])
    # 绑定里的 refs（abstain 时顶层可能为空）
    if not refs:
        for b in answer.claim_bindings or []:
            refs.extend(list(b.evidence_refs or []))
    # 去重保序
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

    if support:
        label_zh = {
            "supported": "有已上线依据支持",
            "insufficient": "依据不足，不能下强结论",
            "contradicted": "存在明确反证",
        }.get(support, support)
        line = f"依据判定：{label_zh}"
        if reason and support != "supported":
            line += f"（{reason}）"
        meta_lines.append(line)

    if uniq_refs:
        meta_lines.append("证据（对齐本答 Claim）：")
        for r in uniq_refs[:8]:
            meta_lines.append(f"· {_humanize_ref(r)}")
    elif support in ("insufficient", "contradicted"):
        meta_lines.append(
            "证据：当前可见已上线内容不足以直接支持该主张"
            + ("；详见上方判定。" if issue else "。")
        )
    elif answer.intent in ("ask_published", "ask_relations") and not answer.refused:
        # 可验证意图却无 refs：标出缺口，避免「AI 说」
        meta_lines.append("证据：本答未附着可核对 EvidenceRef（请视为需复核）。")

    if meta_lines:
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
    if payload and payload.get("claim_support"):
        answer.trace["claim_support"] = payload.get("claim_support")
    if payload and payload.get("issue"):
        answer.trace["issue"] = payload.get("issue")
    return answer


def claim_bindings_summary(bindings: list[ClaimBinding]) -> list[dict[str, Any]]:
    return [b.to_dict() for b in (bindings or [])]
