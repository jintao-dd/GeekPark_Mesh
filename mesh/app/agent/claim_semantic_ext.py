"""Claim semantic extension · v2.4c-2 production（selective）。

三独立维度：
  claim_strength ∈ {weak, strong}
  evidence_entailment ∈ {entailing, direct_related, topical, none, contradicted_by_evidence}
  counter_evidence ∈ {true, false}

cheap deterministic gate → 仅高风险/语义边界调用 LLM judge。
由 assess_claim_support() 正式接入；MESH_CLAIM_SEMANTIC=0 可关闭。

设计约束：
  - 禁止把「装车/量产/批量交付…」扩成关键词黑名单作为最终判定
  - Gate 只用结构/体貌/认知框架（完成态断言形态、证明框架、已有强规则）
  - direct_related ≠ supported；topic ≠ entailment
"""
from __future__ import annotations

import json
import re
from typing import Any

from . import claim_support as cs

# —— cheap gate：结构/体貌，不是产品词表 ——
_SOFT_ACTIVITY = re.compile(
    r"(做了什么|是什么|有哪些|怎么样|如何评价|为什么|谁是|讲了什么|提到了什么)"
)

_PROOF_FRAME = re.compile(
    r"("
    r"能否证明|能否确认|资料证明|资料能否|"
    r"确认.{0,24}(已经|已)|根据.{0,20}确认|"
    r"证明.{0,36}(已经|已)|说明.{0,16}(已经|已|它已)"
    r")"
)

_COMPLETED_ASPECT = re.compile(
    r"("
    r"是否已经|是否已有|是否已(?!经)|"
    r"(已经|已)(实现|完成|进入|采用|交付|落地|成功|批量|确认)|"
    r"已有[\u4e00-\u9fff]{0,14}采用|"
    r"(已经|已)[\u4e00-\u9fff]{0,12}(落地|交付|采用|实现|完成|进入|阶段)"
    r")"
)

_FUTURE_CERTAINTY = re.compile(r"(一定会|必然会|确定会|下周一定)")

_FORCED_CLAIM_FRAME = re.compile(
    r"("
    r"当成.{0,30}证据|"
    r"确认发布|"
    r"明天会发布|将于明天发布|"
    r"任意条目"
    r")"
)

JUDGE_SYSTEM = """Mesh Claim→Evidence 语义评审（selective）。只根据主张与已上线 evidence 摘录判断；禁止库外猜测；不要写用户答案。

分开三个维度：
1) claim_strength: strong=断言已完成/成立的现实状态；weak=普通询问活动/提及
2) evidence_entailment: entailing|direct_related|topical|none|contradicted_by_evidence
   （direct_related ≠ supported；topic ≠ entailment）
3) counter_evidence: true=有明确反证；false=无明确反证

support: supported|insufficient|contradicted
- strong 仅当 entailing 且无反证 → supported；否则 insufficient
- weak：entailing/direct_related/topical → supported
- contradicted：仅 counter_evidence 或 contradicted_by_evidence

只输出紧凑 JSON：claim_strength, evidence_entailment, counter_evidence, support, rationale(≤40字)
"""


def cheap_semantic_gate(query: str) -> dict[str, Any]:
    """便宜筛选：是否进入 selective semantic judge。"""
    q = query or ""
    reasons: list[str] = []
    soft = bool(_SOFT_ACTIVITY.search(q))

    if cs.is_universal_denial_query(q):
        reasons.append("universal_denial")
    if cs.is_strong_deterministic_claim(q):
        reasons.append("det_strong_claim")
    if _FUTURE_CERTAINTY.search(q):
        reasons.append("future_certainty")
    if _PROOF_FRAME.search(q):
        reasons.append("proof_frame")
    if _FORCED_CLAIM_FRAME.search(q):
        reasons.append("forced_claim_frame")
    if _COMPLETED_ASPECT.search(q) and not soft:
        reasons.append("completed_aspect")

    if soft and not any(
        r in reasons for r in ("universal_denial", "det_strong_claim", "future_certainty")
    ):
        reasons = [
            r
            for r in reasons
            if r not in ("proof_frame", "completed_aspect", "forced_claim_frame")
        ]

    return {
        "enter": bool(reasons),
        "reasons": reasons,
        "soft_activity": soft,
    }


def _env_int(name: str, default: int, *, lo: int, hi: int) -> int:
    import os

    try:
        v = int((os.environ.get(name) or "").strip() or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


def _ctx_snip(contexts: list[dict], limit: int | None = None) -> str:
    n = _env_int("MESH_SEMANTIC_SNIP_N", 5, lo=2, hi=8) if limit is None else limit
    body_n = _env_int("MESH_SEMANTIC_SNIP_CHARS", 150, lo=20, hi=220)
    lines = []
    for i, c in enumerate(contexts[:n], 1):
        title = (c.get("标题") or c.get("title") or "").strip()[:80]
        body = (c.get("内容") or c.get("body") or c.get("snippet") or "").strip()[:body_n]
        lines.append(f"[{i}] {title}\n{body}")
    return "\n\n".join(lines) if lines else "(无 evidence 摘录)"


def _parse_judge(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        data = raw
    else:
        text = (raw or "").strip()
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return {"ok": False, "error": "parse_fail", "raw": text[:400]}
        try:
            data = json.loads(m.group(0))
        except Exception:
            return {"ok": False, "error": "json_fail", "raw": text[:400]}

    strength = str(data.get("claim_strength") or "").strip().lower()
    if strength not in ("strong", "weak"):
        strength = "unknown"

    ent = str(data.get("evidence_entailment") or "").strip().lower()
    ent_aliases = {
        "direct": "direct_related",
        "direct/entailing": "entailing",
        "related-only": "topical",
        "related_only": "topical",
        "topical/related-only": "topical",
        "contradicted": "contradicted_by_evidence",
    }
    ent = ent_aliases.get(ent, ent)
    if ent not in (
        "entailing",
        "direct_related",
        "topical",
        "none",
        "contradicted_by_evidence",
    ):
        ent = "unknown"

    ce = data.get("counter_evidence")
    if isinstance(ce, str):
        ce = ce.strip().lower() in ("1", "true", "yes", "是")
    elif ce is None:
        ce = False
    else:
        ce = bool(ce)

    support = str(data.get("support") or "").strip().lower()
    if support not in ("supported", "insufficient", "contradicted"):
        support = "unknown"

    composed = compose_support(strength, ent, ce)
    if composed != "unknown" and support != composed and support != "unknown":
        final = composed
        note = f"support_overridden_by_dims:{support}->{composed}"
    elif composed != "unknown":
        final = composed
        note = "composed_from_dims"
    else:
        final = support
        note = "llm_support_raw"

    return {
        "ok": True,
        "claim_strength": strength,
        "evidence_entailment": ent,
        "counter_evidence": ce,
        "support_llm": support,
        "support": final,
        "compose_note": note,
        "rationale": str(data.get("rationale") or "")[:120],
    }


def compose_support(
    claim_strength: str,
    evidence_entailment: str,
    counter_evidence: bool,
) -> str:
    """三维度 → support；direct_related 对强 claim 不得升 supported。"""
    if counter_evidence or evidence_entailment == "contradicted_by_evidence":
        return "contradicted"
    if claim_strength == "strong":
        if evidence_entailment == "entailing":
            return "supported"
        return "insufficient"
    if claim_strength == "weak":
        if evidence_entailment in ("entailing", "direct_related", "topical"):
            return "supported"
        return "insufficient"
    return "unknown"


def llm_semantic_judge(query: str, contexts: list[dict] | None = None) -> dict[str, Any]:
    from .. import llm

    user = (
        f"主张/问句：\n{query}\n\n"
        f"evidence：\n{_ctx_snip(list(contexts or []))}\n\n"
        "输出 JSON。"
    )
    max_tok = _env_int("MESH_SEMANTIC_MAX_TOKENS", 160, lo=80, hi=500)
    try:
        raw = llm.call(JUDGE_SYSTEM, user, max_tokens=max_tok, json_mode=True, task="semantic")
    except Exception as e:
        return {
            "ok": False,
            "error": f"llm_error:{type(e).__name__}:{e}",
            "claim_strength": "unknown",
            "evidence_entailment": "unknown",
            "counter_evidence": False,
            "support": "unknown",
        }
    return _parse_judge(raw)


def _conservative_fallback(det: dict[str, Any], gate: dict[str, Any], error: str) -> dict[str, Any]:
    out = dict(det)
    fallback_support = det.get("support")
    if gate.get("enter") and det.get("claim_strength") == "weak" and det.get("support") == "supported":
        fallback_support = "insufficient"
        out["claim_strength"] = "strong"
        out["speculative_certainty"] = True
    out["support"] = fallback_support
    out["reason"] = f"semantic_llm_fail:{error}"
    out["semantic"] = {
        "enabled": True,
        "path": "llm_fail_conservative",
        "gate": gate,
        "error": error,
    }
    return out


def apply_semantic_extension(
    query: str,
    contexts: list[dict],
    det: dict[str, Any],
) -> dict[str, Any]:
    """生产路径：在 deterministic 结果上叠加 selective semantic judge。"""
    gate = cheap_semantic_gate(query)
    if not gate["enter"]:
        out = dict(det)
        out["semantic"] = {"enabled": True, "path": "det_passthrough", "gate": gate}
        return out

    judge = llm_semantic_judge(query, contexts)
    if not judge.get("ok"):
        return _conservative_fallback(det, gate, str(judge.get("error") or "unknown"))

    out = dict(det)
    strength = judge.get("claim_strength") or det.get("claim_strength")
    support = judge.get("support") or det.get("support")
    ent = judge.get("evidence_entailment")
    ce = bool(judge.get("counter_evidence"))

    path = "semantic_judge"
    if det.get("universal_denial") and det.get("support") == "contradicted":
        if support != "contradicted":
            support = "contradicted"
            ce = True
            ent = "contradicted_by_evidence"
            path = "det_counter_evidence_preserved"

    # 高风险完成态/证明框架：禁止 false-support
    # 1) LLM 低估为 weak+supported
    # 2) LLM 高估 entailing→supported，但 det 并无 direct_support
    high_risk = any(
        r in (gate.get("reasons") or [])
        for r in (
            "completed_aspect",
            "proof_frame",
            "forced_claim_frame",
            "future_certainty",
            "det_strong_claim",
            "universal_denial",
        )
    )
    det_direct_ok = (
        det.get("support") == "supported"
        and det.get("claim_strength") == "strong"
        and det.get("reason") == "direct_support_for_strong_claim"
    )
    if high_risk and support == "supported" and not det_direct_ok:
        support = "insufficient"
        strength = "strong"
        if ent in (None, "passthrough", "unknown", "entailing"):
            ent = "direct_related" if ent == "entailing" else (ent or "topical")
        path = "gate_high_risk_block_false_support"
    elif high_risk and support == "supported" and strength == "weak":
        strength = "strong"
        support = "insufficient"
        path = "gate_conservative_override"
    elif high_risk and support == "supported" and ent in ("topical", "direct_related", "none"):
        support = "insufficient"
        strength = "strong"
        path = "gate_entailment_override"

    out["claim_strength"] = strength
    out["support"] = support
    out["evidence_entailment"] = ent
    out["counter_evidence"] = ce
    out["speculative_certainty"] = bool(det.get("speculative_certainty") or strength == "strong")
    out["reason"] = f"semantic_ext:{path}:{judge.get('compose_note') or ''}"
    out["semantic"] = {
        "enabled": True,
        "path": path,
        "gate": gate,
        "rationale": judge.get("rationale"),
        "compose_note": judge.get("compose_note"),
        "det_support": det.get("support"),
        "det_strength": det.get("claim_strength"),
        "det_reason": det.get("reason"),
    }
    return out


def assess_claim_support_shadow(
    query: str,
    *,
    contexts: list[dict] | None = None,
    evidence_refs: list[str] | None = None,
    force_llm: bool = False,
) -> dict[str, Any]:
    """诊断用：对比 det vs semantic overlay。"""
    ctxs = list(contexts or [])
    refs = list(evidence_refs or [])
    det = cs.assess_claim_support_deterministic(query, contexts=ctxs, evidence_refs=refs)
    gate = cheap_semantic_gate(query)
    enter = bool(force_llm or gate["enter"])

    out: dict[str, Any] = {
        "det": {
            "claim_strength": det.get("claim_strength"),
            "support": det.get("support"),
            "reason": det.get("reason"),
            "speculative_certainty": det.get("speculative_certainty"),
            "universal_denial": det.get("universal_denial"),
        },
        "gate": gate,
        "llm_invoked": False,
        "shadow": {
            "claim_strength": det.get("claim_strength"),
            "evidence_entailment": "passthrough",
            "counter_evidence": False,
            "support": det.get("support"),
            "path": "det_passthrough",
        },
    }
    if not enter:
        return out

    applied = apply_semantic_extension(query, ctxs, det)
    out["llm_invoked"] = True
    sem = applied.get("semantic") or {}
    out["shadow"] = {
        "claim_strength": applied.get("claim_strength"),
        "evidence_entailment": applied.get("evidence_entailment") or "passthrough",
        "counter_evidence": bool(applied.get("counter_evidence")),
        "support": applied.get("support"),
        "path": sem.get("path"),
        "rationale": sem.get("rationale"),
        "error": sem.get("error"),
    }
    return out
