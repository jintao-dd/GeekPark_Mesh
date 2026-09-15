"""Relation decision audit：结构化「每个 candidate 为何留/丢」。"""
from __future__ import annotations

from typing import Any

# 代码 Gate 机器可读原因码
GATE_CODE_PURE_CO = "pure_entity_cooccurrence"
GATE_CODE_NO_SHARED_ANCHOR = "no_shared_anchor"
GATE_CODE_LLM_SKIP = "llm_skip"
GATE_CODE_INVALID_CAND = "invalid_candidate_id"
GATE_CODE_INVALID_LABEL = "invalid_label"
GATE_CODE_MISSING_REFS = "missing_evidence_refs"
GATE_CODE_REFS_BAD = "evidence_refs_not_resolvable"
GATE_CODE_SINGLE_TEAM = "evidence_single_team"
GATE_CODE_PROVENANCE = "cross_team_provenance_failed"
GATE_CODE_MISMATCH = "evidence_team_source_mismatch"
GATE_CODE_DECISION_INCONSISTENT = "decision_inconsistent"
GATE_CODE_DECISION_MISSING = "decision_missing"

_KNOWN_GATE_CODES = frozenset({
    GATE_CODE_PURE_CO,
    GATE_CODE_NO_SHARED_ANCHOR,
    GATE_CODE_LLM_SKIP,
    GATE_CODE_INVALID_CAND,
    GATE_CODE_MISSING_REFS,
    GATE_CODE_REFS_BAD,
    GATE_CODE_SINGLE_TEAM,
    GATE_CODE_PROVENANCE,
    GATE_CODE_MISMATCH,
    GATE_CODE_DECISION_INCONSISTENT,
    GATE_CODE_DECISION_MISSING,
})

_GATE_CODE_LABELS: dict[str, str] = {
    GATE_CODE_PURE_CO: "纯实体共现（次实体未形成跨团队事链）→ 硬 skip",
    GATE_CODE_NO_SHARED_ANCHOR: "两侧 evidence 无共享锚点（同一公司/人/项目）",
    GATE_CODE_LLM_SKIP: "LLM 主动 skip",
    GATE_CODE_INVALID_CAND: "candidate_id 无效",
    GATE_CODE_INVALID_LABEL: "label 不在 17 标签内",
    GATE_CODE_MISSING_REFS: "LLM 未给出 evidence_refs",
    GATE_CODE_REFS_BAD: "evidence_refs 无法解析为 evidence",
    GATE_CODE_SINGLE_TEAM: "evidence 仅单团队",
    GATE_CODE_PROVENANCE: "跨团队 provenance 不独立",
    GATE_CODE_MISMATCH: "teams/sources 与 evidence 不一致",
    GATE_CODE_DECISION_INCONSISTENT: "Decision 的 relation_type/label/reason 互相矛盾",
    GATE_CODE_DECISION_MISSING: "LLM 未返回 relation_decision（漏答，非 skip）",
    "gate_would_cooccur": "（已废弃）旧版纯共现警告；现改为硬 skip",
    "missing_narrative_title_or_body": "Narrative 缺 title/body",
    "filtered_after_gate": "Gate 通过后又被 pipeline 滤掉",
    "title_dedupe": "Narrative 标题去重",
}

_OUTCOME_LABELS: dict[str, str] = {
    "published": "读者页展示",
    "backlog": "draft backlog（已写未展示）",
    "skipped_llm": "LLM 主动跳过",
    "skipped_gate_override": "LLM 想留，Gate 事实约束拦截",
    "dropped_narrative": "Gate 通过但 Writer 未产出",
    "dropped_post_gate": "Gate 通过后 pipeline 丢弃",
    "skipped_gate": "Gate 跳过",
    "decision_missing": "LLM 漏答 decision",
}


def parse_gate_reason_code(gate_reason: str, *, llm_decision: str | None) -> str:
    reason = (gate_reason or "").strip()
    if reason == GATE_CODE_DECISION_MISSING:
        return GATE_CODE_DECISION_MISSING
    llm = (llm_decision or "").strip().lower()
    if llm not in ("keep", "skip"):
        return GATE_CODE_DECISION_MISSING
    if llm != "keep":
        return GATE_CODE_LLM_SKIP
    if reason in _KNOWN_GATE_CODES:
        return reason
    if reason.startswith("invalid_label"):
        return GATE_CODE_INVALID_LABEL
    return "llm_editorial_reject_at_gate"


def gate_reason_detail(code: str, gate_reason: str, llm_reason: str) -> str:
    if code == GATE_CODE_LLM_SKIP:
        return (llm_reason or gate_reason or "LLM skip").strip()
    if code in _GATE_CODE_LABELS:
        base = _GATE_CODE_LABELS[code]
        if code == GATE_CODE_PURE_CO:
            return base
        if llm_reason and code not in (GATE_CODE_LLM_SKIP,):
            return f"{base}；LLM 理由：{llm_reason}"
        return base
    return (gate_reason or llm_reason or code).strip()


def _narrative_skip_map(audit: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in audit.get("narrative_skipped") or []:
        cid = (row.get("candidate_id") or "").strip()
        if cid:
            out[cid] = (row.get("reason") or "missing_narrative_title_or_body").strip()
    return out


def _post_gate_drop_map(audit: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in audit.get("post_gate_drops") or []:
        cid = (row.get("candidate_id") or "").strip()
        if cid:
            out[cid] = (row.get("reason") or "filtered_after_gate").strip()
    return out


def _published_map(relations: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        if not r.get("reader_visible"):
            continue
        cid = (r.get("candidate_id") or "").strip()
        if cid:
            out[cid] = r
    return out


def _all_relations_map(relations: list[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        cid = (r.get("candidate_id") or "").strip()
        if cid:
            out[cid] = r
    return out


def enrich_audit_row(
    row: dict,
    *,
    published_by_id: dict[str, dict],
    all_relations_by_id: dict[str, dict],
    narrative_skipped: dict[str, str],
    post_gate_drops: dict[str, str],
) -> dict[str, Any]:
    """单行 candidate → 可读 ledger 条目。"""
    r = dict(row)
    cid = (r.get("candidate_id") or "").strip()
    llm_dec = (r.get("llm_decision") or "").strip().lower()
    gate_dec = (r.get("gate_decision") or "").strip().lower()
    gate_reason = (r.get("gate_reason") or "").strip()
    llm_reason = (r.get("llm_reason") or "").strip()
    decision_missing = bool(r.get("decision_missing")) or r.get("decision_outcome") == "missing"

    code = r.get("gate_reason_code") or parse_gate_reason_code(gate_reason, llm_decision=r.get("llm_decision"))
    detail = r.get("gate_reason_detail") or gate_reason_detail(code, gate_reason, llm_reason)
    gate_overrode = bool(r.get("gate_overrode_llm")) or (llm_dec == "keep" and gate_dec == "skip")
    would_cooccur = bool(r.get("gate_would_cooccur"))

    pub = published_by_id.get(cid)
    rel = all_relations_by_id.get(cid) or pub
    reader_visible = bool((rel or {}).get("reader_visible"))
    draft_kept = cid in all_relations_by_id
    published = reader_visible and pub is not None

    skip_reason_code = None
    if not published and not draft_kept:
        if decision_missing:
            skip_reason_code = GATE_CODE_DECISION_MISSING
        elif llm_dec != "keep":
            skip_reason_code = GATE_CODE_LLM_SKIP
        elif gate_dec == "skip":
            skip_reason_code = code

    if decision_missing:
        final_outcome = "decision_missing"
        decided_by = "llm_coverage"
    elif published:
        final_outcome = "published"
        decided_by = "reader"
    elif draft_kept and gate_dec == "keep" and not reader_visible:
        final_outcome = "backlog"
        decided_by = "display"
    elif cid in narrative_skipped:
        final_outcome = "dropped_narrative"
        decided_by = "narrative"
        code = narrative_skipped[cid]
        detail = _GATE_CODE_LABELS.get(code, code)
    elif cid in post_gate_drops:
        final_outcome = "dropped_post_gate"
        decided_by = "post_pipeline"
        code = post_gate_drops[cid]
        detail = _GATE_CODE_LABELS.get(code, code)
    elif llm_dec != "keep":
        final_outcome = "skipped_llm"
        decided_by = "llm"
    elif gate_overrode:
        final_outcome = "skipped_gate_override"
        decided_by = "gate"
    else:
        final_outcome = "skipped_gate"
        decided_by = "gate"

    r.update({
        "skip_reason_code": skip_reason_code,
        "gate_reason_code": code,
        "gate_reason_detail": detail,
        "gate_overrode_llm": gate_overrode,
        "gate_would_cooccur": would_cooccur,
        "decision_keep": llm_dec == "keep",
        "decision_skip": llm_dec != "keep",
        "gate_keep": gate_dec == "keep",
        "gate_skip": gate_dec != "keep",
        "published_outcome": published,
        "decision_tier": (rel or {}).get("decision_tier") or row.get("decision_tier"),
        "reader_visible": reader_visible,
        "draft_kept": draft_kept,
        "pipeline": {
            "raw_candidate": True,
            "decision_candidate": r.get("decision_candidate", True),
            "decision_outcome": r.get("decision_outcome") or llm_dec,
            "decision_tier": (rel or {}).get("decision_tier") or row.get("decision_tier"),
            "gate_outcome": r.get("gate_outcome") or gate_dec,
            "draft_kept": draft_kept,
            "reader_visible": reader_visible,
            "published": published,
        },
        "decided_by": decided_by,
        "final_outcome": final_outcome,
        "outcome_label": _OUTCOME_LABELS.get(final_outcome, final_outcome),
        "published": published,
        "published_title": (pub.get("title") or "").strip() if pub else None,
        "needs_human_review": gate_overrode or would_cooccur or final_outcome == "dropped_narrative" or decision_missing,
        "review_hint": (
            "LLM 漏答 decision，需重跑 Decision 或人工补录"
            if decision_missing
            else (
            "Gate 提示共现风险，交 Owner 决定是否展示"
            if would_cooccur and gate_dec == "keep"
            else (
                "LLM keep 被 Gate 事实约束拦截"
                if gate_overrode
                else (
                    "LLM 主动 skip：若认为应成卡，属 LLM 漏选/误判"
                    if final_outcome == "skipped_llm"
                    else None
                )
            )
            )
        ),
    })
    return r


def finalize_decision_audit(audit: dict, relations: list[dict] | None = None) -> dict[str, Any]:
    """补全 audit：candidate_ledger + outcome 汇总 + gate_override 清单。"""
    audit = dict(audit or {})
    rels = list(relations or [])
    published_by_id = _published_map(rels)
    all_relations_by_id = _all_relations_map(rels)
    nar_skip = _narrative_skip_map(audit)
    post_drop = _post_gate_drop_map(audit)

    ledger = [
        enrich_audit_row(
            row,
            published_by_id=published_by_id,
            all_relations_by_id=all_relations_by_id,
            narrative_skipped=nar_skip,
            post_gate_drops=post_drop,
        )
        for row in (audit.get("rows") or [])
        if isinstance(row, dict)
    ]

    outcome_counts: dict[str, int] = {}
    for row in ledger:
        k = row.get("final_outcome") or "unknown"
        outcome_counts[k] = outcome_counts.get(k, 0) + 1

    gate_overrides = [r for r in ledger if r.get("gate_overrode_llm")]
    llm_skips = [r for r in ledger if r.get("final_outcome") == "skipped_llm"]
    published_rows = [r for r in ledger if r.get("published")]
    backlog_rows = [r for r in ledger if r.get("final_outcome") == "backlog"]

    skip_by_code: dict[str, int] = {}
    for row in ledger:
        if row.get("published") or row.get("final_outcome") == "backlog":
            continue
        code = row.get("skip_reason_code") or row.get("gate_reason_code") or "unknown"
        skip_by_code[code] = skip_by_code.get(code, 0) + 1

    audit["candidate_ledger"] = ledger
    audit["outcome_summary"] = {
        "n_candidates": audit.get("n_candidates", len(ledger)),
        "n_ledger_rows": len(ledger),
        "n_decision_missing": outcome_counts.get("decision_missing", 0),
        "n_draft_relations": audit.get("n_draft_relations", sum(1 for r in ledger if r.get("draft_kept"))),
        "n_reader_relations": audit.get("n_reader_relations", len(published_rows)),
        "n_backlog_relations": audit.get("n_backlog_relations", len(backlog_rows)),
        "n_published": len(published_rows),
        "n_skipped_llm": outcome_counts.get("skipped_llm", 0),
        "n_skipped_gate_override": outcome_counts.get("skipped_gate_override", 0),
        "n_gate_would_cooccur": sum(1 for r in ledger if r.get("gate_would_cooccur")),
        "n_dropped_narrative": outcome_counts.get("dropped_narrative", 0),
        "n_dropped_post_gate": outcome_counts.get("dropped_post_gate", 0),
        "by_outcome": outcome_counts,
        "by_skip_code": skip_by_code,
        "by_tier": audit.get("display_summary", {}).get("by_tier", {}),
    }
    audit["backlog_rows"] = backlog_rows
    audit["gate_overrides"] = gate_overrides
    audit["llm_skips"] = llm_skips
    audit["published_rows"] = published_rows
    return audit


def build_human_report(audit: dict, relations: list[dict] | None = None) -> dict[str, Any]:
    """供 deploy 脚本 / 控制台输出的人类可读报告。"""
    full = finalize_decision_audit(audit, relations)
    summary = full.get("outcome_summary") or {}
    lines = []
    for row in full.get("candidate_ledger") or []:
        cid = row.get("candidate_id")
        title = row.get("candidate_title") or "?"
        outcome = row.get("outcome_label") or "?"
        detail = row.get("gate_reason_detail") or row.get("llm_reason") or ""
        hint = row.get("review_hint")
        pub = f" → {row['published_title']}" if row.get("published_title") else ""
        line = f"[{cid}] {title} | {outcome}{pub} | {detail}"
        if hint:
            line += f" ⚠ {hint}"
        lines.append(line)

    return {
        "outcome_summary": summary,
        "gate_overrides": full.get("gate_overrides") or [],
        "llm_skips": full.get("llm_skips") or [],
        "published_rows": full.get("published_rows") or [],
        "candidate_ledger": full.get("candidate_ledger") or [],
        "ledger_lines": lines,
    }
