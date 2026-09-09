"""Claim ↔ Evidence support（轻量；不改 Agent Contract / Retrieval）。

support ∈ {supported, insufficient, contradicted}
有 EvidenceRef ≠ claim 已被支持。
"""
from __future__ import annotations

import re
from typing import Any, Literal

SupportLabel = Literal["supported", "insufficient", "contradicted"]

_ABSTAIN = (
    "资料未提供可核对依据，不能确认该结论，不能下结论。"
    "已上线记录若仅涉及相关主体，也不等于支持该主张。"
)

# 用户要求确定性未来/完成态、但周报通常无法担保的问法
_SPECULATIVE_CERTAINTY = re.compile(
    r"(一定会|必然会|确定会|已经成功发布|已经完成|全面量产|下周一定|确认.{0,12}已经|"
    r"资料证明.{0,20}一定|根据.{0,10}确认.{0,20}已经)"
)

# 证据正文需出现才可视为「支持该确定性主张」的强信号（极少）
_STRONG_SUPPORT = re.compile(
    r"(已完成交割|交割完成|已经成功发布|全面量产|正式量产|下周将完成融资交割)"
)

_CONTRADICT = re.compile(r"(并未发布|没有发布|尚未量产|未完成交割|否认|不会发布)")


def is_speculative_certainty_query(query: str) -> bool:
    return bool(_SPECULATIVE_CERTAINTY.search(query or ""))


def _blob(contexts: list[dict]) -> str:
    parts: list[str] = []
    for c in contexts or []:
        for k in ("标题", "title", "内容", "body", "snippet", "摘要"):
            v = c.get(k)
            if v:
                parts.append(str(v))
    return "\n".join(parts)


def assess_claim_support(
    query: str,
    *,
    contexts: list[dict] | None = None,
    evidence_refs: list[str] | None = None,
    answer: str = "",
) -> dict[str, Any]:
    """判定 support；返回结构化字段供 Gold / harness / adapter 共用。"""
    q = query or ""
    blob = _blob(contexts or [])
    refs = list(evidence_refs or [])
    speculative = is_speculative_certainty_query(q)

    entity = ""
    for m in re.finditer(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_.-]{2,}", q):
        t = m.group(0)
        if t not in ("根据", "资料", "确认", "已经", "下周", "一定", "完成", "是否", "什么"):
            entity = t
            break

    relation = "speculative_certainty" if speculative else "mention"
    temporal = "future_certain" if re.search(r"下周|即将|一定会", q) else "unspecified"
    provenance = "published_only"

    if _CONTRADICT.search(blob) and speculative:
        label: SupportLabel = "contradicted"
    elif speculative:
        if blob and _STRONG_SUPPORT.search(blob):
            label = "supported"
        else:
            # 有命中主体相关条目仍不足以支持确定性 claim
            label = "insufficient"
    elif refs or blob:
        label = "supported"
    else:
        label = "insufficient"

    return {
        "entity": entity,
        "relation": relation,
        "temporal": temporal,
        "evidence_refs": refs[:12],
        "provenance": provenance,
        "support": label,
        "speculative_certainty": speculative,
        "reason": (
            "evidence_contradicts_claim"
            if label == "contradicted"
            else (
                "speculative_certainty_without_strong_support"
                if speculative and label == "insufficient"
                else ("has_published_evidence" if label == "supported" else "no_evidence")
            )
        ),
    }


def abstain_answer_for_unsupported_claim(assessment: dict[str, Any]) -> str | None:
    """当 support 不足以支撑确定性 claim 时返回拒答话术。"""
    if assessment.get("support") in ("insufficient", "contradicted") and assessment.get(
        "speculative_certainty"
    ):
        return _ABSTAIN
    return None
