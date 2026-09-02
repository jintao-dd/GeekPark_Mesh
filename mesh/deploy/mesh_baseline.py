#!/usr/bin/env python3
"""Mesh Baseline scoreboard — freeze + compare Δ (calibrated v1.1).

口径原则（P0）：
  - 无金标时指标必须带 _proxy / _e2e_ 命名，禁止冒充真实 Recall/Precision
  - Token / Latency 不可重复计数；每题只取一个 canonical latency
  - Relation Grounding = grounded/total（0 卡 ≠ 1.0）
  - failure_rate = 真实失败比例，不是整包 pass 的 0/1
  - Baseline JSON 保存逐题 / ledger 明细 + 实验环境（含 git_dirty）

用法（mesh/ 目录）:
  python deploy/mesh_baseline.py build \\
    --ask-report eval/reports/report_XXXX.json \\
    --regression-report eval/reports/full_regression_XXXX.json \\
    [--relation-audit path/to/audit.json] \\
    --out eval/reports/BASELINE_v1.json

  python deploy/mesh_baseline.py --compare BASELINE.json OPTIMIZED.json --md-out EVAL_DELTA.md

Exit: 0 OK · 2 BLOCKER · 1 usage error
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "app" / "prompts"
EVAL_SET = ROOT / "eval" / "ask_eval_v1.jsonl"

# ---------------------------------------------------------------------------
# Metric policy (must match docs/MESH_BASELINE_v1.md)
# ---------------------------------------------------------------------------

# New worse than Baseline → merge blocker
HARD_BLOCKERS_HIGHER = frozenset({
    "ask_grounding",
    "rel_grounding",
})
HARD_BLOCKERS_LOWER = frozenset({
    "rel_strong_no_evidence",
    "rel_blocked_leaks",
    "rel_missing_tier",
})

HIGHER_IS_BETTER = frozenset({
    "ask_e2e_pass_rate",
    "ask_precision_proxy",
    "ask_grounding",
    "rel_candidate_keep_rate_proxy",
    "rel_decision_gate_accept_rate_proxy",
    "rel_grounding",
})
LOWER_IS_BETTER = frozenset({
    "ask_p50_ms",
    "ask_p95_ms",
    "ask_p99_ms",
    "ask_token_total",
    "human_review_count",
    "generation_cost_proxy",
    "failure_rate",
    "retry_rate",
    "rel_strong_no_evidence",
    "rel_blocked_leaks",
    "rel_missing_tier",
})

# Old → new key (compare still works on frozen files from first draft)
LEGACY_METRIC_ALIASES = {
    "ask_recall": "ask_e2e_pass_rate",
    "ask_precision": "ask_precision_proxy",
    "rel_candidate_recall_proxy": "rel_candidate_keep_rate_proxy",
    "rel_decision_precision_proxy": "rel_decision_gate_accept_rate_proxy",
}

DISPLAY_ORDER = [
    ("ask_e2e_pass_rate", "Ask E2E Pass Rate (not gold Recall)"),
    ("ask_precision_proxy", "Ask Precision (proxy)"),
    ("ask_grounding", "Ask Grounding"),
    ("ask_p50_ms", "Ask P50 ms (canonical)"),
    ("ask_p95_ms", "Ask P95 ms (canonical)"),
    ("ask_p99_ms", "Ask P99 ms (canonical)"),
    ("ask_token_total", "Ask Token"),
    ("rel_candidate_keep_rate_proxy", "Rel Candidate Keep Rate (proxy)"),
    ("rel_decision_gate_accept_rate_proxy", "Rel Gate Accept Rate (proxy)"),
    ("rel_grounding", "Rel Grounding"),
    ("human_review_count", "Human Review"),
    ("generation_cost_proxy", "Generation Cost"),
    ("failure_rate", "Failure Rate"),
    ("retry_rate", "Retry Rate"),
    ("rel_strong_no_evidence", "Rel strong_no_evidence"),
    ("rel_blocked_leaks", "Rel blocked_leaks"),
    ("rel_missing_tier", "Rel missing_tier"),
]

STATUS_OK = "[OK]"
STATUS_BLOCKER = "[BLOCKER]"
STATUS_WARN = "[WARN]"
STATUS_SAME = "[.]"
STATUS_NA = "-"

SCHEMA = "mesh_baseline_v1.1"


def _pctile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return float(sorted_vals[f])
    return float(sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f))


def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


def _git_meta() -> dict[str, Any]:
    repo = ROOT.parent if (ROOT.parent / ".git").exists() else ROOT

    def _run(args: list[str]) -> str:
        try:
            return subprocess.check_output(
                args, cwd=repo, text=True, encoding="utf-8", errors="replace",
            ).strip()
        except Exception:
            return ""

    commit = _run(["git", "rev-parse", "HEAD"]) or "unknown"
    # Freeze gate: only tracked modifications count as dirty (untracked eval reports OK).
    tracked = _run(["git", "status", "--porcelain", "-uno"])
    porcelain = _run(["git", "status", "--porcelain"])
    dirty = bool(tracked)
    diff = _run(["git", "diff", "HEAD"])
    staged = _run(["git", "diff", "--cached"])
    blob = (diff + "\n" + staged + "\n" + tracked).encode("utf-8", errors="replace")
    return {
        "commit": commit,
        "git_dirty": dirty,
        "git_diff_hash": _sha256_bytes(blob) if dirty else None,
        "git_untracked_present": bool(porcelain) and not dirty,
        "branch": _run(["git", "rev-parse", "--abbrev-ref", "HEAD"]) or None,
    }


def _prompt_hashes() -> dict[str, str]:
    out: dict[str, str] = {}
    if not PROMPTS.is_dir():
        return out
    for p in sorted(PROMPTS.glob("*.md")):
        out[p.name] = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
    return out


def _schema_version() -> str | None:
    try:
        from app.db import SCHEMA_VERSION
        return str(SCHEMA_VERSION)
    except Exception:
        return None


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_metrics(m: dict[str, Any]) -> dict[str, Any]:
    """Map legacy metric keys → v1.1 names."""
    out = dict(m)
    for old, new in LEGACY_METRIC_ALIASES.items():
        if old in out and new not in out:
            out[new] = out[old]
    return out


def _canonical_latency_ms(q: dict) -> tuple[float | None, str | None]:
    """每题只取一个 latency。优先 e2e/total；仅检索报告时回退 recall.latency_ms。"""
    lat = q.get("latency")
    if isinstance(lat, dict):
        for k in ("total_ms", "e2e_ms", "ms", "wall_ms"):
            if lat.get(k) is not None:
                return float(lat[k]), f"latency.{k}"
    if isinstance(lat, (int, float)):
        return float(lat), "latency"
    rec = q.get("recall") or {}
    if rec.get("latency_ms") is not None:
        return float(rec["latency_ms"]), "recall.latency_ms"
    return None, None


def _stage_latencies(q: dict) -> dict[str, float | None]:
    lat = q.get("latency") if isinstance(q.get("latency"), dict) else {}
    keys = ("retrieve_ms", "source_ms", "cross_ms", "verify_ms", "generation_ms")
    return {k: (float(lat[k]) if lat.get(k) is not None else None) for k in keys}


def _token_from_usage(us: dict | None) -> int:
    if not isinstance(us, dict):
        return 0
    if us.get("total_tokens") is not None:
        return int(us["total_tokens"])
    return int(us.get("prompt_tokens") or 0) + int(us.get("completion_tokens") or 0)


def _ask_bundle(ask_report: dict) -> tuple[dict[str, Any], list[dict], dict[str, Any]]:
    qs = [q for q in (ask_report.get("questions") or []) if isinstance(q, dict)]
    n = len(qs)
    if n == 0:
        return {}, [], {"ask_n": 0}

    n_pass = sum(1 for q in qs if q.get("pass"))
    n_bad_prec = sum(
        1 for q in qs
        if not q.get("pass")
        and (q.get("failure_layer") or "") in ("intent", "answer", "citation", "e2e")
    )
    n_ground_fail = sum(
        1 for q in qs
        if not q.get("pass")
        and (q.get("failure_layer") or "") in ("grounding", "citation", "hallucination")
    )

    cases: list[dict] = []
    lats: list[float] = []
    tokens = 0
    retries = 0
    llm_calls = 0
    stage_lists: dict[str, list[float]] = {
        "retrieve_ms": [], "source_ms": [], "cross_ms": [], "verify_ms": [], "generation_ms": [],
    }

    latency_sources: dict[str, int] = {}
    for q in qs:
        canon, lat_src = _canonical_latency_ms(q)
        stages = _stage_latencies(q)
        us = q.get("usage") if isinstance(q.get("usage"), dict) else {}
        tok = _token_from_usage(us)
        tokens += tok
        rtry_raw = q.get("n_retries")
        if rtry_raw is None:
            rtry_raw = us.get("n_retries")
        rtry = int(rtry_raw or 0)
        calls = int(q.get("n_llm_calls") or us.get("n_calls") or us.get("llm_calls") or (1 if tok else 0))
        retries += rtry
        llm_calls += calls
        if canon is not None:
            lats.append(canon)
        if lat_src:
            latency_sources[lat_src] = latency_sources.get(lat_src, 0) + 1
        for sk, sv in stages.items():
            if sv is not None:
                stage_lists[sk].append(sv)

        cases.append({
            "id": q.get("id"),
            "pass": bool(q.get("pass")),
            "failure_layer": q.get("failure_layer"),
            "latency_ms": canon,
            "latency_source": lat_src,
            "latency_stages_ms": stages,
            "tokens": tok or None,
            "n_retries": (rtry if rtry_raw is not None else None),
            "recall_mode": (q.get("recall") or {}).get("mode"),
            "n_context": (q.get("recall") or {}).get("n_context"),
        })

    lats_sorted = sorted(lats)
    stage_pct: dict[str, Any] = {}
    for sk, vals in stage_lists.items():
        vs = sorted(vals)
        stage_pct[sk] = {
            "p50": _pctile(vs, 50),
            "p95": _pctile(vs, 95),
            "p99": _pctile(vs, 99),
            "n": len(vs),
        }

    metrics = {
        # NOT gold retrieval recall — E2E case pass rate only
        "ask_e2e_pass_rate": round(n_pass / n, 4),
        "ask_precision_proxy": round(1.0 - (n_bad_prec / n), 4),
        "ask_grounding": round(1.0 - (n_ground_fail / n), 4),
        "ask_p50_ms": _pctile(lats_sorted, 50),
        "ask_p95_ms": _pctile(lats_sorted, 95),
        "ask_p99_ms": _pctile(lats_sorted, 99),
        "ask_token_total": tokens or None,
        "ask_n": n,
        "ask_n_pass": n_pass,
        "ask_n_fail": n - n_pass,
        "ask_latency_n_samples": len(lats),  # must equal ask_n if every q has latency
        "ask_latency_sources": latency_sources or None,
    }
    saw_retry_fields = any(
        (q.get("n_retries") is not None)
        or ((q.get("usage") or {}).get("n_retries") is not None)
        for q in qs
    )
    retry_rate = None
    if saw_retry_fields and llm_calls > 0:
        retry_rate = round(retries / max(llm_calls, 1), 4)
    elif ask_report.get("n_retries") is not None and ask_report.get("n_llm_calls"):
        retry_rate = round(
            float(ask_report["n_retries"]) / max(float(ask_report["n_llm_calls"]), 1), 4,
        )

    extras = {
        "ask_latency_stages": stage_pct,
        "ask_retries_total": retries if saw_retry_fields else None,
        "ask_llm_calls_total": llm_calls if saw_retry_fields else None,
        "retry_rate": retry_rate,
        "ask_latency_warning": (
            "report lacks latency.total_ms; P50/P95 used recall.latency_ms (retrieve-only). "
            "Freeze Baseline only from Ask --e2e reports."
            if latency_sources and all(str(s).startswith("recall.") for s in latency_sources)
            else None
        ),
    }
    return metrics, cases, extras


def _parse_pytest_counts(tail: str) -> tuple[int | None, int | None]:
    """Return (passed, failed) from pytest -q tail if present."""
    if not tail:
        return None, None
    m = re.search(r"(\d+)\s+passed", tail)
    passed = int(m.group(1)) if m else None
    f = re.search(r"(\d+)\s+failed", tail)
    failed = int(f.group(1)) if f else 0
    if passed is None and failed == 0:
        return None, None
    return passed or 0, failed


def _rel_bundle(
    reg: dict,
    relation_audit: dict | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    checks = reg.get("checks") or {}
    integ = checks.get("relation_integrity_draft") or checks.get("relation_integrity_published") or {}
    fields = checks.get("new_fields") or {}
    audit = fields.get("audit_summary") or {}
    if not isinstance(audit, dict):
        audit = {}
    if relation_audit:
        # prefer full audit if provided
        if relation_audit.get("outcome_summary"):
            audit = {**audit, **(relation_audit.get("outcome_summary") or {})}
        elif relation_audit.get("_relation_decision_audit"):
            inner = relation_audit["_relation_decision_audit"]
            audit = {**audit, **(inner.get("outcome_summary") or {})}

    n_for = int(audit.get("n_candidates_for_decision") or audit.get("n_candidates") or 0)
    n_draft = fields.get("n_draft_relations")
    if n_draft is None:
        n_draft = integ.get("n_relations") or 0
    n_draft = int(n_draft or 0)

    # Funnel proxy: when a real audit is attached, use its draft/candidate counts
    # (do not mix store integrity's n_relations with a different decision ledger).
    n_draft_funnel = n_draft
    if relation_audit is not None and "n_draft_relations" in audit:
        n_draft_funnel = int(audit.get("n_draft_relations") or 0)

    # keep rate among candidates sent to decision — NOT gold recall
    keep_rate = (n_draft_funnel / n_for) if n_for > 0 else None

    n_override = audit.get("n_skipped_gate_override")
    n_keep_llm = audit.get("n_published")
    if n_keep_llm is None:
        n_keep_llm = n_draft_funnel if relation_audit is not None else n_draft
    n_skip_gate = int(n_override or 0)
    # among LLM-keep that reached gate: accept rate = kept / (kept + gate_override)
    denom = int(n_keep_llm or 0) + n_skip_gate
    gate_accept = (int(n_keep_llm or 0) / denom) if denom > 0 else None

    n_rel = int(integ.get("n_relations") or n_draft or 0)
    n_ok = integ.get("n_ok")
    if n_ok is None and n_rel:
        # fall back: assume bad = strong_no + can't use team_mismatch as card count
        n_ok = max(0, n_rel - int(integ.get("strong_no_evidence") or 0))
    n_ok = int(n_ok or 0)

    strong_no = int(integ.get("strong_no_evidence") or 0)
    blocked = int(integ.get("blocked_leaks") or 0)
    missing_tier = len(fields.get("missing_decision_tier") or [])
    team_mis = int(integ.get("team_mismatch") or 0)

    # Grounding = share of relations that pass integrity (n_ok / n_rel)
    # 0 relations → None (NOT 1.0)
    if n_rel <= 0:
        rel_grounding = None
    else:
        rel_grounding = round(n_ok / n_rel, 4)

    human = int(fields.get("n_draft_backlog") or 0) + missing_tier

    # failure_rate from check-level outcomes (not whole-report boolean)
    check_items = [c for c in checks.values() if isinstance(c, dict) and "ok" in c]
    n_checks = len(check_items)
    n_check_fail = sum(1 for c in check_items if not c.get("ok"))
    # also fold pytest counts if present
    for key in ("pytest", "relation_pytest"):
        tail = (checks.get(key) or {}).get("tail") or ""
        p, f = _parse_pytest_counts(tail)
        if p is not None:
            n_checks += p + (f or 0)
            n_check_fail += f or 0

    failure_rate = round(n_check_fail / n_checks, 4) if n_checks else None

    metrics = {
        "rel_candidate_keep_rate_proxy": round(keep_rate, 4) if keep_rate is not None else None,
        "rel_decision_gate_accept_rate_proxy": round(gate_accept, 4) if gate_accept is not None else None,
        "rel_grounding": rel_grounding,
        "rel_strong_no_evidence": strong_no,
        "rel_blocked_leaks": blocked,
        "rel_missing_tier": missing_tier,
        "rel_team_mismatch": team_mis,
        "human_review_count": human,
        "generation_cost_proxy": None,
        "n_candidates_for_decision": n_for or None,
        "n_draft_relations": n_draft_funnel if relation_audit is not None else n_draft,
        "n_draft_relations_store": n_draft if relation_audit is not None else None,
        "n_relations_scanned": n_rel,
        "n_relations_grounded_ok": n_ok,
        "failure_rate": failure_rate,
        "regression_checks_total": n_checks or None,
        "regression_checks_failed": n_check_fail if n_checks else None,
    }

    ledger = []
    if relation_audit:
        ledger = (
            relation_audit.get("candidate_ledger")
            or (relation_audit.get("_relation_decision_audit") or {}).get("candidate_ledger")
            or relation_audit.get("rows")
            or []
        )

    cases = {
        "integrity": {
            "n_relations": n_rel,
            "n_ok": n_ok,
            "strong_no_evidence": strong_no,
            "blocked_leaks": blocked,
            "team_mismatch": team_mis,
            "missing_tier": missing_tier,
            "bad_titles": (integ.get("bad_titles") or [])[:20],
        },
        "candidate_ledger": ledger[:200] if isinstance(ledger, list) else [],
        "audit_summary": audit,
        "decision_tier_counts": fields.get("decision_tier_counts"),
    }
    return metrics, cases, {}


def build_env(*, extra: dict | None = None) -> dict[str, Any]:
    git = _git_meta()
    env: dict[str, Any] = {
        **git,
        "provider": os.environ.get("MESH_LLM_PROVIDER") or "openai_compat",
        "llm_model": os.environ.get("MESH_LLM_MODEL"),
        "llm_base_url_host": (os.environ.get("MESH_LLM_BASE_URL") or "").split("//")[-1].split("/")[0] or None,
        "temperature": os.environ.get("MESH_LLM_TEMPERATURE"),
        "embedding_model": os.environ.get("MESH_EMBED_MODEL") or os.environ.get("MESH_EMBEDDING_MODEL"),
        "prompt_hashes": _prompt_hashes(),
        "eval_dataset_hash": _sha256_file(EVAL_SET),
        "schema_version": _schema_version(),
        "topk": os.environ.get("MESH_RETRIEVE_TOPK"),
        "rerank": os.environ.get("MESH_RERANK") or os.environ.get("MESH_ASK_RERANK"),
        "ask_concurrency": os.environ.get("MESH_ASK_LLM_CONCURRENCY"),
        "ask_global": os.environ.get("MESH_ASK_LLM_GLOBAL"),
        "job_concurrency": os.environ.get("MESH_JOB_LLM_CONCURRENCY"),
        "job_global": os.environ.get("MESH_JOB_LLM_GLOBAL"),
        "candidate_cap": os.environ.get("MESH_RELATION_CANDIDATE_CAP"),
        "chunk_config": {
            "embed_batch": os.environ.get("MESH_EMBED_BATCH"),
            "embed_enabled": os.environ.get("MESH_EMBED_ENABLED") or os.environ.get("MESH_VECTOR_ENABLED"),
        },
        "analysis": {
            "MESH_ASK_ANALYSIS": os.environ.get("MESH_ASK_ANALYSIS", "1"),
            "MESH_ANALYSIS_MAX_GROUPS": os.environ.get("MESH_ANALYSIS_MAX_GROUPS", "4"),
            "MESH_ANALYSIS_MAX_PER_GROUP": os.environ.get("MESH_ANALYSIS_MAX_PER_GROUP", "6"),
        },
        "max_tokens": {
            "extract": 8000,
            "card": 4000,
            "issue_draft": 16000,
            "relation_decisions": 8000,
        },
    }
    if extra:
        env.update(extra)
    return env


def build_baseline(
    *,
    ask_report: dict | None,
    regression_report: dict | None,
    relation_audit: dict | None = None,
    label: str = "v1.1",
) -> dict:
    metrics: dict[str, Any] = {}
    cases: dict[str, Any] = {"ask": [], "relation": {}}
    notes_proxy = [
        "ask_e2e_pass_rate — NOT gold retrieval recall; case pass/n only",
        "ask_precision_proxy — failure_layer heuristic until gold",
        "rel_candidate_keep_rate_proxy — draft/candidates; NOT gold candidate recall",
        "rel_decision_gate_accept_rate_proxy — gate accept among keep path; NOT gold decision precision",
    ]

    if ask_report:
        m, ask_cases, extras = _ask_bundle(ask_report)
        metrics.update(m)
        if extras.get("retry_rate") is not None:
            metrics["retry_rate"] = extras["retry_rate"]
        cases["ask"] = ask_cases
        cases["ask_latency_stages"] = extras.get("ask_latency_stages")
        if extras.get("ask_latency_warning"):
            notes_proxy.append(extras["ask_latency_warning"])

    if regression_report:
        m, rel_cases, _ = _rel_bundle(regression_report, relation_audit)
        metrics.update({k: v for k, v in m.items() if k != "failure_rate" or "failure_rate" not in metrics})
        # prefer regression failure_rate if present
        if m.get("failure_rate") is not None:
            metrics["failure_rate"] = m["failure_rate"]
        cases["relation"] = rel_cases

    if metrics.get("retry_rate") is None:
        notes_proxy.append("retry_rate — null until reports expose n_retries/n_llm_calls")
    if metrics.get("generation_cost_proxy") is None:
        notes_proxy.append("generation_cost_proxy — null until pricing table is wired (P2)")
    if metrics.get("ask_token_total") is None:
        notes_proxy.append("ask_token_total — null: Ask usage lacked total_tokens/prompt+completion")
    elif ask_report:
        n_tok = sum(
            1 for q in (ask_report.get("questions") or [])
            if isinstance(q, dict) and (
                (q.get("usage") or {}).get("total_tokens") is not None
                or (q.get("usage") or {}).get("prompt_tokens") is not None
            )
        )
        n_q = len([q for q in (ask_report.get("questions") or []) if isinstance(q, dict)])
        if n_q and n_tok < n_q:
            notes_proxy.append(
                f"ask_token_total — partial coverage {n_tok}/{n_q} questions had token usage "
                "(direct/structured paths may skip LLM)"
            )

    return {
        "baseline": label,
        "schema": SCHEMA,
        "frozen_at": datetime.now(timezone.utc).isoformat(),
        "corpus": (regression_report or {}).get("corpus")
        or (ask_report or {}).get("corpus_env")
        or "unknown",
        "slug": (regression_report or {}).get("slug"),
        "ask_eval_set": "ask_eval_v1.jsonl",
        "env": build_env(),
        "metrics": metrics,
        "hard_blockers": {
            "higher_is_better_must_not_drop": sorted(HARD_BLOCKERS_HIGHER),
            "lower_is_better_must_not_rise": sorted(HARD_BLOCKERS_LOWER),
        },
        "notes": {
            "proxy": notes_proxy,
            "do_not_freeze_as_v1_until_calibrated": False,
            "latency_rule": "one canonical end-to-end latency_ms per ask case; stages separate",
            "token_rule": "prefer total_tokens else prompt+completion; never both",
            "rel_grounding_rule": "n_ok/n_relations; 0 relations => null (not 1.0)",
        },
        "cases": cases,
        "raw": {
            "ask_report_summary": {
                "generated_at": (ask_report or {}).get("generated_at"),
                "corpus_env": (ask_report or {}).get("corpus_env"),
                "retrieval_summary": (ask_report or {}).get("retrieval_summary"),
            } if ask_report else None,
            "regression_summary": {
                "generated_at": (regression_report or {}).get("generated_at"),
                "slug": (regression_report or {}).get("slug"),
                "corpus": (regression_report or {}).get("corpus"),
                "pass": (regression_report or {}).get("pass"),
                "failures": (regression_report or {}).get("failures"),
            } if regression_report else None,
        },
    }


def _fmt(v: Any) -> str:
    if v is None:
        return "-"
    if isinstance(v, float):
        if abs(v) >= 100:
            return f"{v:.1f}"
        return f"{v:.4f}".rstrip("0").rstrip(".")
    return str(v)


def _delta(old: Any, new: Any) -> tuple[Any, str]:
    if old is None or new is None:
        return None, "-"
    try:
        d = float(new) - float(old)
    except (TypeError, ValueError):
        return None, "-"
    return d, _fmt(d) if abs(d) >= 0.00005 else "0"


def _status(key: str, old: Any, new: Any, delta: Any) -> str:
    if old is None or new is None or delta is None:
        return STATUS_NA
    try:
        o, n, d = float(old), float(new), float(delta)
    except (TypeError, ValueError):
        return STATUS_NA

    if key in HARD_BLOCKERS_LOWER:
        if n > o + 1e-9:
            return STATUS_BLOCKER
        if n < o - 1e-9:
            return STATUS_OK
        return STATUS_SAME

    if key in HARD_BLOCKERS_HIGHER:
        if n + 1e-9 < o:
            return STATUS_BLOCKER
        return STATUS_OK if abs(d) < 1e-9 or d > 0 else STATUS_OK

    if key in HIGHER_IS_BETTER:
        if d > 1e-9:
            return STATUS_OK
        if d < -1e-9:
            return STATUS_WARN
        return STATUS_SAME

    if key in LOWER_IS_BETTER:
        if d < -1e-9:
            return STATUS_OK
        if d > 1e-9:
            return STATUS_WARN
        return STATUS_SAME

    return STATUS_SAME


def compare(base: dict, new: dict) -> tuple[list[dict], bool]:
    bm = _normalize_metrics(base.get("metrics") or {})
    nm = _normalize_metrics(new.get("metrics") or {})
    rows: list[dict] = []
    blocked = False
    labels = dict(DISPLAY_ORDER)

    for key, label in DISPLAY_ORDER:
        if key not in bm and key not in nm:
            continue
        o, n = bm.get(key), nm.get(key)
        d, ds = _delta(o, n)
        st = _status(key, o, n, d)
        if st == STATUS_BLOCKER:
            blocked = True
        rows.append({
            "metric": label,
            "key": key,
            "baseline": o,
            "new": n,
            "delta": d,
            "delta_s": ds,
            "status": st,
        })
    return rows, blocked


def print_table(rows: list[dict], *, base_meta: dict, new_meta: dict) -> None:
    be = base_meta.get("env") or {}
    ne = new_meta.get("env") or {}
    print("Mesh Baseline Compare (%s)" % SCHEMA)
    print(
        f"  baseline: {base_meta.get('baseline')} @ {(be.get('commit') or base_meta.get('commit') or '')[:10]} "
        f"dirty={be.get('git_dirty')} corpus={base_meta.get('corpus')}"
    )
    print(
        f"  new:      {new_meta.get('baseline')} @ {(ne.get('commit') or new_meta.get('commit') or '')[:10]} "
        f"dirty={ne.get('git_dirty')} corpus={new_meta.get('corpus')}"
    )
    if be.get("git_dirty") or ne.get("git_dirty"):
        print("  WARNING: git_dirty=true — snapshot may not be reproducible from commit alone.")
    print()
    hdr = f"{'Metric':<42} {'Baseline':>10} {'New':>10} {'Delta':>10} {'Status':<12}"
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        print(
            f"{r['metric']:<42} {_fmt(r['baseline']):>10} {_fmt(r['new']):>10} "
            f"{r['delta_s']:>10} {r['status']:<12}"
        )
    print()


def cmd_build(args: argparse.Namespace) -> int:
    ask = _load(Path(args.ask_report)) if args.ask_report else None
    reg = _load(Path(args.regression_report)) if args.regression_report else None
    audit = _load(Path(args.relation_audit)) if args.relation_audit else None
    if not ask and not reg:
        print("need --ask-report and/or --regression-report", file=sys.stderr)
        return 1
    doc = build_baseline(
        ask_report=ask,
        regression_report=reg,
        relation_audit=audit,
        label=args.label,
    )
    doc["raw"] = doc.get("raw") or {}
    doc["raw"]["inputs"] = {
        "ask_report": str(args.ask_report) if args.ask_report else None,
        "regression_report": str(args.regression_report) if args.regression_report else None,
        "relation_audit": str(args.relation_audit) if args.relation_audit else None,
    }
    if doc["env"].get("git_dirty") and not args.allow_dirty:
        print(
            "ERROR: git working tree is dirty. Commit/stash first, or pass --allow-dirty "
            "(Baseline will record git_dirty=true / git_diff_hash).",
            file=sys.stderr,
        )
        return 1
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} schema={SCHEMA}")
    print("metrics:", json.dumps(doc.get("metrics"), ensure_ascii=False, indent=2))
    print(f"ask_cases={len(doc.get('cases', {}).get('ask') or [])} "
          f"ledger_rows={len((doc.get('cases', {}).get('relation') or {}).get('candidate_ledger') or [])}")
    for note in (doc.get("notes") or {}).get("proxy") or []:
        if "latency.total_ms" in note or "Freeze Baseline" in note:
            print("WARNING:", note)
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    base = _load(Path(args.baseline))
    new = _load(Path(args.new))
    if "metrics" not in base:
        base = {"metrics": base, "baseline": "raw", "env": {}, "corpus": ""}
    if "metrics" not in new:
        new = {"metrics": new, "baseline": "raw", "env": {}, "corpus": ""}

    rows, blocked = compare(base, new)
    print_table(rows, base_meta=base, new_meta=new)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"blocked": blocked, "rows": rows, "schema": SCHEMA}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.md_out:
        lines = [
            "### Eval Delta",
            "",
            f"_schema: {SCHEMA}_",
            "",
            "| Metric | Baseline | New | Delta | Status |",
            "|--------|---------:|----:|------:|--------|",
        ]
        for r in rows:
            lines.append(
                f"| {r['metric']} | {_fmt(r['baseline'])} | {_fmt(r['new'])} | {r['delta_s']} | {r['status']} |"
            )
        be, ne = base.get("env") or {}, new.get("env") or {}
        lines += [
            "",
            f"**Env:** `{be.get('commit')}` dirty={be.get('git_dirty')} / `{ne.get('commit')}` dirty={ne.get('git_dirty')} "
            f"corpus `{base.get('corpus')}` -> `{new.get('corpus')}`",
            "",
            f"**Verdict:** {'REJECT (Grounding/safety blocker)' if blocked else 'FILL: accept / reject / canary'}",
            "",
        ]
        Path(args.md_out).write_text("\n".join(lines), encoding="utf-8")
        print(f"wrote {args.md_out}")

    if blocked:
        print("RESULT: [BLOCKER] Grounding or safety worse than baseline.")
        return 2
    warns = sum(1 for r in rows if r["status"] == STATUS_WARN)
    if warns:
        print(f"RESULT: [WARN] {warns} tradeoff regression(s) — declare accepted trades in PR.")
    else:
        print("RESULT: [OK] no hard blockers.")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser(description="Mesh Baseline v1.1 freeze & compare")
    sub = ap.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="Build calibrated baseline JSON from reports")
    b.add_argument("--ask-report")
    b.add_argument("--regression-report")
    b.add_argument("--relation-audit", help="Optional decision audit / ledger JSON")
    b.add_argument("--out", required=True)
    b.add_argument("--label", default="v1.1")
    b.add_argument("--allow-dirty", action="store_true", help="Allow dirty git tree (recorded in env)")
    b.set_defaults(func=cmd_build)

    c = sub.add_parser("compare", help="Compare two baseline JSON files")
    c.add_argument("baseline")
    c.add_argument("new")
    c.add_argument("--json-out", default=None)
    c.add_argument("--md-out", default=None)
    c.set_defaults(func=cmd_compare)

    if len(sys.argv) >= 2 and sys.argv[1] == "--compare":
        sys.argv = [sys.argv[0], "compare", *sys.argv[2:]]

    args = ap.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()
