"""Claim ↔ Evidence support（轻量；不改 Agent Contract / Retrieval）。

support ∈ {supported, insufficient, contradicted}
有 EvidenceRef ≠ claim 已被支持。

语义门：
  strong claim  ≠  topic/entity overlap → 不得标 supported
  contradicted  需要明确反证；insufficient 不能仅凭相关证据升级
"""
from __future__ import annotations

import re
from typing import Any, Literal

SupportLabel = Literal["supported", "insufficient", "contradicted"]

_ABSTAIN = (
    "资料未提供可核对依据，不能确认该结论，不能下结论。"
    "已上线记录若仅涉及相关主体，也不等于支持该主张。"
)

# —— Claim strength：强确定性主张（不是「含量产就拒」；无直接证据时不得升级）——
_STRONG_CLAIM = re.compile(
    r"("
    r"已经量产|已量产|量产上车|已经上车|已上车|"
    r"能否证明.{0,30}量产|证明.{0,30}量产|证明.{0,30}上车|"
    r"一定会|必然会|确定会|下周一定|"
    r"已经成功发布|全面量产|全球量产|正式量产|出货百万|"
    r"融资交割|已经交割|交割完成|"
    r"确认.{0,12}已经|资料证明.{0,20}一定|根据.{0,10}确认.{0,20}已经"
    r")"
)

# 直接支持「量产/上车」类强 claim 的证据措辞（topic 讨论不算）
_DIRECT_MASS_PROD = re.compile(
    r"(已经量产|已量产上车|量产上车|已经上车|已上车|正式量产|全面量产|全球量产|出货百万)"
)
_DIRECT_FINANCING = re.compile(r"(已完成交割|交割完成|融资已完成|完成融资交割)")
_DIRECT_RELEASE = re.compile(r"(已经成功发布|已正式发布|全面量产)")

# 兼容旧名
_SPECULATIVE_CERTAINTY = _STRONG_CLAIM

# 「从未/没有」类全称否定（有接触证据时应 contradicted）
_UNIVERSAL_DENIAL = re.compile(
    r"(从未被?接触|从来没有被?接触|从未出现在|资料证明.{0,16}从未|确认.{0,10}从未)"
)

_CONTRADICT_FACT = re.compile(r"(并未发布|没有发布|尚未量产|未完成交割|否认|不会发布)")
_CONTACT_EVIDENCE = re.compile(r"(已接触|接触过|沟通过|拜访|有对话记录|跟进)")

_ENTITY_SKIP = {
    "根据",
    "资料",
    "确认",
    "已经",
    "下周",
    "一定",
    "完成",
    "是否",
    "什么",
    "证明",
    "能否",
    "讨论",
    "相关",
    "记录",
    "培训",
    "周报",
    "已有",
}


def is_speculative_certainty_query(query: str) -> bool:
    """强确定性 claim（含量产/交割/必然等）。"""
    return bool(_STRONG_CLAIM.search(query or ""))


def is_strong_deterministic_claim(query: str) -> bool:
    return is_speculative_certainty_query(query)


def is_universal_denial_query(query: str) -> bool:
    return bool(_UNIVERSAL_DENIAL.search(query or ""))


def extract_claim_entity(query: str) -> str:
    """从 query 反推主体；优先「X从未…」与较长专名。"""
    q = query or ""
    m = re.search(
        r"([\u4e00-\u9fffA-Za-z][^\s，。？?]{1,16}?)(?:从未|从来没有|下周一定|已经量产|已量产)",
        q,
    )
    if m:
        ent = re.sub(r"^(资料证明|根据已有周报确认|根据周报确认|确认)", "", m.group(1)).strip()
        if len(ent) >= 2:
            return ent
    # 面壁智能 / 程天科技 / Founder Park 等 3+ 字优先
    for m in re.finditer(r"[\u4e00-\u9fff]{3,8}|[A-Za-z][A-Za-z0-9_.-]{2,}", q):
        t = m.group(0)
        if t not in _ENTITY_SKIP and not t.startswith("证明"):
            return t
    for m in re.finditer(r"[\u4e00-\u9fff]{2,8}", q):
        t = m.group(0)
        if t not in _ENTITY_SKIP:
            return t
    return ""


def _blob(contexts: list[dict]) -> str:
    parts: list[str] = []
    for c in contexts or []:
        for k in ("标题", "title", "内容", "body", "snippet", "摘要"):
            v = c.get(k)
            if v:
                parts.append(str(v))
    return "\n".join(parts)


def contexts_with_contact_counter_evidence(
    contexts: list[dict],
    *,
    entity: str = "",
) -> list[dict]:
    """挑选能反驳「从未接触」的 published 上下文。"""
    out: list[dict] = []
    for c in contexts or []:
        blob = " ".join(
            str(c.get(k) or "")
            for k in ("标题", "title", "内容", "body", "snippet", "摘要")
        )
        if not _CONTACT_EVIDENCE.search(blob):
            continue
        if entity and entity not in blob:
            # 允许标题含实体简称
            title = str(c.get("标题") or c.get("title") or "")
            if entity not in title and entity[:2] not in blob:
                continue
        out.append(c)
    return out


def evidence_directly_supports_strong_claim(query: str, blob: str) -> bool:
    """强 claim 必须有直接措辞；topic overlap 不算。"""
    q = query or ""
    b = blob or ""
    if not b:
        return False
    if re.search(r"量产|上车", q):
        return bool(_DIRECT_MASS_PROD.search(b))
    if re.search(r"交割|融资", q):
        return bool(_DIRECT_FINANCING.search(b))
    if re.search(r"发布|GPU-99|量产", q):
        return bool(_DIRECT_RELEASE.search(b) or _DIRECT_MASS_PROD.search(b))
    return bool(_DIRECT_MASS_PROD.search(b) or _DIRECT_FINANCING.search(b) or _DIRECT_RELEASE.search(b))


def assess_claim_support(
    query: str,
    *,
    contexts: list[dict] | None = None,
    evidence_refs: list[str] | None = None,
    answer: str = "",
) -> dict[str, Any]:
    """判定 support；返回结构化字段供 Gold / harness / adapter 共用。"""
    q = query or ""
    ctxs = list(contexts or [])
    blob = _blob(ctxs)
    refs = list(evidence_refs or [])
    strong = is_strong_deterministic_claim(q)
    denial = is_universal_denial_query(q)
    entity = extract_claim_entity(q)

    if denial:
        relation = "universal_denial"
    elif strong:
        relation = "strong_deterministic"
    else:
        relation = "mention"
    temporal = "future_certain" if re.search(r"下周|即将|一定会", q) else "unspecified"
    provenance = "published_only"

    label: SupportLabel
    reason = "no_evidence"
    claim_strength = "strong" if (strong or denial) else "weak"

    # 1) 全称否定：明确接触反证 → contradicted
    if denial:
        counters = contexts_with_contact_counter_evidence(ctxs, entity=entity)
        if counters or (blob and _CONTACT_EVIDENCE.search(blob) and (not entity or entity[:2] in blob)):
            label = "contradicted"
            reason = "evidence_contradicts_universal_denial"
        else:
            label = "insufficient"
            reason = "denial_not_provable_from_published"
    # 2) 强确定性 claim：必须直接支持，否则 insufficient（相关≠支持）
    elif strong:
        if _CONTRADICT_FACT.search(blob):
            label = "contradicted"
            reason = "evidence_contradicts_claim"
        elif evidence_directly_supports_strong_claim(q, blob):
            label = "supported"
            reason = "direct_support_for_strong_claim"
        else:
            label = "insufficient"
            reason = "strong_claim_without_direct_evidence"
    # 3) 弱 claim / 普通提及
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
        "claim_strength": claim_strength,
        "speculative_certainty": strong,  # 兼容旧字段
        "universal_denial": denial,
        "reason": reason,
    }


def abstain_answer_for_unsupported_claim(assessment: dict[str, Any]) -> str | None:
    """Answer safety：强 claim / 否认类在 insufficient|contradicted 时拒答。

    注意：Evidence label 与 Answer safety 分离——contradicted 与 insufficient
    都可以在 Answer 层 abstain。
    """
    risky = (
        assessment.get("speculative_certainty")
        or assessment.get("universal_denial")
        or assessment.get("claim_strength") == "strong"
    )
    if assessment.get("support") in ("insufficient", "contradicted") and risky:
        return _ABSTAIN
    return None


def enrich_contexts_for_denial_counter_evidence(
    con,
    ask,
    query: str,
    contexts: list[dict],
) -> list[dict]:
    """否认类 claim：对齐明确反证（接触类 published 上下文）。

    仅 enrichment，不改 Retrieval 默认路径；无反证则原样返回 → insufficient。
    """
    if not is_universal_denial_query(query):
        return contexts
    entity = extract_claim_entity(query)
    counters = contexts_with_contact_counter_evidence(contexts, entity=entity)
    if counters:
        rest = [c for c in contexts if c not in counters]
        return counters + rest

    # 二次探针：实体 + 接触（Published-only AskScope 不变）
    from .. import ask_engine

    probe_q = f"{entity} 接触".strip() if entity else "接触"
    try:
        prepared2 = ask_engine.prepare(con, probe_q, ask)
        contexts2 = list(prepared2.get("contexts") or [])
    except Exception:
        return contexts
    counters = contexts_with_contact_counter_evidence(contexts2, entity=entity)
    if not counters:
        # 实体过严时放宽：任意含接触且含实体二字
        counters = contexts_with_contact_counter_evidence(contexts2, entity="")
        if entity:
            counters = [
                c
                for c in counters
                if entity[:2]
                in " ".join(str(c.get(k) or "") for k in ("标题", "title", "内容", "body", "snippet", "摘要"))
            ]
    if not counters:
        return contexts
    # 反证置前，保留原 contexts
    seen = set()
    merged: list[dict] = []
    for c in counters + list(contexts):
        key = (c.get("item_id"), c.get("标题") or c.get("title"), (c.get("内容") or c.get("body") or "")[:40])
        if key in seen:
            continue
        seen.add(key)
        merged.append(c)
    return merged