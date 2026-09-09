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
    r"(一定会|必然会|确定会|已经成功发布|已经完成|全面量产|全球量产|下周一定|"
    r"确认.{0,12}已经|资料证明.{0,20}一定|根据.{0,10}确认.{0,20}已经|"
    r"已量产上车|融资交割|出货百万|正式量产)"
)

# 「从未/没有」类全称否定（有接触证据时应 contradicted）
_UNIVERSAL_DENIAL = re.compile(
    r"(从未被?接触|从来没有被?接触|从未出现在|资料证明.{0,12}从未|确认.{0,8}从未)"
)

# 证据正文需出现才可视为「支持该确定性主张」的强信号（极少）
_STRONG_SUPPORT = re.compile(
    r"(已完成交割|交割完成|已经成功发布|全面量产|正式量产|下周将完成融资交割|出货百万)"
)

_CONTRADICT_FACT = re.compile(r"(并未发布|没有发布|尚未量产|未完成交割|否认|不会发布)")
_CONTACT_EVIDENCE = re.compile(r"(接触|已接触|沟通过|拜访|跟进)")


def is_speculative_certainty_query(query: str) -> bool:
    return bool(_SPECULATIVE_CERTAINTY.search(query or ""))


def is_universal_denial_query(query: str) -> bool:
    return bool(_UNIVERSAL_DENIAL.search(query or ""))


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
    denial = is_universal_denial_query(q)

    entity = ""
    for m in re.finditer(r"[\u4e00-\u9fff]{2,8}|[A-Za-z][A-Za-z0-9_.-]{2,}", q):
        t = m.group(0)
        if t not in ("根据", "资料", "确认", "已经", "下周", "一定", "完成", "是否", "什么", "证明"):
            entity = t
            break

    if denial:
        relation = "universal_denial"
    elif speculative:
        relation = "speculative_certainty"
    else:
        relation = "mention"
    temporal = "future_certain" if re.search(r"下周|即将|一定会", q) else "unspecified"
    provenance = "published_only"

    label: SupportLabel
    reason = "no_evidence"

    if denial and blob and _CONTACT_EVIDENCE.search(blob):
        # 全称否定被接触类证据反驳
        label = "contradicted"
        reason = "evidence_contradicts_universal_denial"
    elif speculative and _CONTRADICT_FACT.search(blob):
        label = "contradicted"
        reason = "evidence_contradicts_claim"
    elif speculative:
        if blob and _STRONG_SUPPORT.search(blob):
            label = "supported"
            reason = "strong_support_phrase"
        else:
            label = "insufficient"
            reason = "speculative_certainty_without_strong_support"
    elif denial:
        # 否定主张但无反证/无证据 → 仍不足以「证明从未」
        label = "insufficient"
        reason = "denial_not_provable_from_published"
    elif refs or blob:
        label = "supported"
        reason = "has_published_evidence"
    else:
        label = "insufficient"
        reason = "no_evidence"

    return {
        "entity": entity,
        "relation": relation,
        "temporal": temporal,
        "evidence_refs": refs[:12],
        "provenance": provenance,
        "support": label,
        "speculative_certainty": speculative,
        "universal_denial": denial,
        "reason": reason,
    }


def abstain_answer_for_unsupported_claim(assessment: dict[str, Any]) -> str | None:
    """当 support 不足以支撑高风险 claim 时返回拒答话术。"""
    risky = assessment.get("speculative_certainty") or assessment.get("universal_denial")
    if assessment.get("support") in ("insufficient", "contradicted") and risky:
        return _ABSTAIN
    return None
