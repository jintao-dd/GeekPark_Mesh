"""Eval Dashboard — read-only view of frozen baselines / experiments.

绝对禁止：触发 Preview / Ask / Embed / Publish / 任何写库。
只读：mesh/eval/reports 下的 BASELINE_*.json / OPTIMIZED_*.json。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"

CURRENT_POINTER = "BASELINE_v1_current.json"
PRODUCTION_POINTER = "PRODUCTION_current.json"
LATEST_ROLES = ("baseline", "latest", "production")

STAGE_CN = {
    "retrieve_ms": "检索",
    "source_ms": "溯源",
    "cross_ms": "交叉",
    "verify_ms": "校验",
    "generation_ms": "生成",
}

# 边界审查记分板（与最近一次人工审查一致；Dashboard 不美化、不隐藏 WARN）
BOUNDARY_SCORECARD = [
    {"area": "强关系才进读者页", "verdict": "PASS"},
    {"area": "草稿 / 已发布分离", "verdict": "PASS"},
    {"area": "已发布禁止再 Preview", "verdict": "PASS"},
    {"area": "Preview 不污染 Ask 索引", "verdict": "PASS"},
    {"area": "Publish 才写检索索引", "verdict": "PASS"},
    {"area": "Decision 与 Gate 分工", "verdict": "PASS"},
    {"area": "LLM 覆盖率", "verdict": "PASS"},
    {
        "area": "审计字段命名",
        "verdict": "WARN",
        "note": "审计 schema 有小命名限制（by_tier / published 命名），不影响核心结论，暂不修以免动指标语义。",
    },
    {"area": "Baseline v1 已冻结", "verdict": "PASS"},
    {"area": "集成测 A–D", "verdict": "PASS"},
]

# Dashboard 展示用中文指标名（CLI 仍用英文 DISPLAY_ORDER）
METRIC_CN = {
    "ask_e2e_pass_rate": "Ask 端到端通过率",
    "ask_precision_proxy": "Ask 精确度（代理）",
    "ask_grounding": "Ask 有据可依",
    "ask_p50_ms": "Ask 延迟 P50",
    "ask_p95_ms": "Ask 延迟 P95",
    "ask_p99_ms": "Ask 延迟 P99",
    "ask_token_total": "Ask Token 总量",
    "retry_rate": "重试率",
    "rel_candidate_keep_rate_proxy": "关系候选保留率（代理）",
    "rel_decision_gate_accept_rate_proxy": "关系门控通过率（代理）",
    "rel_grounding": "关系完整性",
    "human_review_count": "待人工复核数",
    "failure_rate": "失败率",
    "rel_strong_no_evidence": "强关系缺证据",
    "rel_blocked_leaks": "被拦关系泄漏",
    "rel_missing_tier": "缺 decision_tier",
}

STATUS_CN = {
    "[OK]": "变好",
    "[WARN]": "变差（可交易）",
    "[BLOCKER]": "阻断发布",
    "[.]": "持平",
    "-": "不可比",
}

SKIP_CODE_CN = {
    "llm_skip": "模型主动跳过",
    "missing_evidence_refs": "缺证据引用",
    "invalid_label": "标签不合法",
    "decision_inconsistent": "决策不一致",
}

RATE_KEYS = frozenset({
    "ask_e2e_pass_rate", "ask_precision_proxy", "ask_grounding", "retry_rate",
    "rel_candidate_keep_rate_proxy", "rel_decision_gate_accept_rate_proxy", "rel_grounding",
    "failure_rate",
})
MS_KEYS = frozenset({"ask_p50_ms", "ask_p95_ms", "ask_p99_ms"})


def _safe_name(name: str | None) -> str | None:
    if not name:
        return None
    name = Path(name).name
    if ".." in name or "/" in name or "\\" in name:
        return None
    if not (
        name.startswith("BASELINE_")
        or name.startswith("OPTIMIZED_")
        or name.startswith("PRODUCTION_")
    ):
        return None
    if not name.endswith(".json"):
        return None
    return name


def fmt_pct(v: Any, *, digits: int = 1) -> str:
    if v is None:
        return "—"
    try:
        pct = float(v) * 100.0
    except (TypeError, ValueError):
        return "—"
    if digits <= 0:
        return f"{pct:.0f}%"
    return f"{pct:.{digits}f}%"


def fmt_sec_ms(v: Any, *, digits: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v) / 1000.0:.{digits}f}秒"
    except (TypeError, ValueError):
        return "—"


def fmt_int(v: Any) -> str:
    if v is None:
        return "—"
    try:
        return f"{int(round(float(v))):,}"
    except (TypeError, ValueError):
        return str(v)


def fmt_metric(key: str, v: Any) -> str:
    if v is None:
        return "—"
    if key in RATE_KEYS:
        return fmt_pct(v, digits=1)
    if key in MS_KEYS:
        return fmt_sec_ms(v, digits=1)
    if key == "ask_token_total":
        return fmt_int(v)
    try:
        f = float(v)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.4g}"
    except (TypeError, ValueError):
        return str(v)


def fmt_delta(key: str, d: Any, ds: str | None = None) -> str:
    if d is None:
        return ds or "—"
    try:
        dv = float(d)
    except (TypeError, ValueError):
        return ds or "—"
    if abs(dv) < 1e-12:
        return "0"
    if key in RATE_KEYS:
        sign = "+" if dv > 0 else ""
        return f"{sign}{dv * 100:.1f}pp"
    if key in MS_KEYS:
        # show seconds delta
        sec = dv / 1000.0
        sign = "+" if sec > 0 else ""
        return f"{sign}{sec:.1f}秒"
    if key == "ask_token_total":
        sign = "+" if dv > 0 else ""
        return f"{sign}{int(round(dv)):,}"
    if abs(dv) < 1e-9:
        return "0"
    return f"{dv:+.4g}"


def list_experiments() -> list[dict[str, Any]]:
    """Scan report dir for freeze / optimized snapshots (newest first)."""
    if not REPORTS.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for p in sorted(REPORTS.glob("BASELINE_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if p.name.endswith("_demo.json") or "smoke" in p.name.lower():
            continue
        rows.append(_brief(p))
    for p in sorted(REPORTS.glob("OPTIMIZED_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        if "demo" in p.name.lower():
            continue
        rows.append(_brief(p))
    for p in sorted(REPORTS.glob("PRODUCTION_*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        rows.append(_brief(p))
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for r in rows:
        if r["name"] in seen:
            continue
        seen.add(r["name"])
        out.append(r)
    return out


def _brief(path: Path) -> dict[str, Any]:
    meta: dict[str, Any] = {"name": path.name, "mtime": path.stat().st_mtime, "path": str(path)}
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        meta["ok"] = False
        return meta
    env = doc.get("env") or {}
    m = doc.get("metrics") or {}
    meta.update({
        "ok": True,
        "label": doc.get("baseline") or path.stem,
        "schema": doc.get("schema"),
        "frozen_at": doc.get("frozen_at"),
        "corpus": doc.get("corpus"),
        "slug": doc.get("slug"),
        "commit": (env.get("commit") or "")[:10] or None,
        "git_dirty": env.get("git_dirty"),
        "is_current_pointer": path.name == CURRENT_POINTER,
        "ask_e2e_frac": (
            f"{m.get('ask_n_pass')}/{m.get('ask_n')}"
            if m.get("ask_n_pass") is not None and m.get("ask_n")
            else fmt_pct(m.get("ask_e2e_pass_rate"), digits=0)
        ),
        "ask_grounding_s": fmt_pct(m.get("ask_grounding"), digits=0),
        "rel_keep_s": fmt_pct(m.get("rel_candidate_keep_rate_proxy"), digits=0),
        "rel_integrity_s": fmt_pct(m.get("rel_grounding"), digits=0),
        "p95_s": fmt_sec_ms(m.get("ask_p95_ms"), digits=1),
        "kind": (
            "production" if path.name.startswith("PRODUCTION_")
            else ("baseline" if path.name.startswith("BASELINE_") else "optimized")
        ),
        "is_production_pointer": path.name == PRODUCTION_POINTER,
    })
    return meta


def load_experiment(name: str | None) -> dict[str, Any] | None:
    safe = _safe_name(name) or CURRENT_POINTER
    path = REPORTS / safe
    if not path.is_file():
        cands = sorted(REPORTS.glob("BASELINE_v1_20*.json"), key=lambda x: x.stat().st_mtime, reverse=True)
        if not cands:
            return None
        path = cands[0]
        safe = path.name
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(doc, dict) or "metrics" not in doc:
        return None
    doc["_file"] = safe
    return doc


def resolve_role_files() -> dict[str, dict[str, Any]]:
    """三态：基线 / 最新实验 / 生产。只解析文件名，不写库。"""
    baseline = CURRENT_POINTER if (REPORTS / CURRENT_POINTER).is_file() else None
    if not baseline:
        dated = sorted(REPORTS.glob("BASELINE_v1_20*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        baseline = dated[0].name if dated else None

    opts = sorted(
        [p for p in REPORTS.glob("OPTIMIZED_*.json") if "demo" not in p.name.lower()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if opts:
        latest = opts[0].name
    else:
        dated = sorted(REPORTS.glob("BASELINE_v1_20*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        latest = dated[0].name if dated else baseline

    has_prod = (REPORTS / PRODUCTION_POINTER).is_file()
    production = PRODUCTION_POINTER if has_prod else baseline
    production_note = None if has_prod else "尚未单独冻 PRODUCTION_current.json，暂与基线同一份。"

    def _meta(name: str | None) -> dict[str, Any]:
        if not name:
            return {"file": None, "ok": False}
        doc = load_experiment(name)
        if not doc:
            return {"file": name, "ok": False}
        env = doc.get("env") or {}
        m = doc.get("metrics") or {}
        return {
            "file": doc.get("_file") or name,
            "ok": True,
            "commit": (env.get("commit") or "")[:7] or None,
            "git_dirty": env.get("git_dirty"),
            "ask_e2e_frac": (
                f"{m.get('ask_n_pass')}/{m.get('ask_n')}"
                if m.get("ask_n_pass") is not None and m.get("ask_n")
                else None
            ),
            "frozen_at": doc.get("frozen_at"),
        }

    return {
        "baseline": {**_meta(baseline), "label": "基线", "role": "baseline"},
        "latest": {**_meta(latest), "label": "最新实验", "role": "latest"},
        "production": {
            **_meta(production),
            "label": "生产",
            "role": "production",
            "note": production_note,
        },
    }


def boundary_summary() -> dict[str, Any]:
    fails = sum(1 for r in BOUNDARY_SCORECARD if r["verdict"] == "FAIL")
    warns = sum(1 for r in BOUNDARY_SCORECARD if r["verdict"] == "WARN")
    passes = sum(1 for r in BOUNDARY_SCORECARD if r["verdict"] == "PASS")
    return {
        "fails": fails,
        "warns": warns,
        "passes": passes,
        "rows": BOUNDARY_SCORECARD,
        "warn_notes": [r.get("note") for r in BOUNDARY_SCORECARD if r["verdict"] == "WARN" and r.get("note")],
    }


def ask_board(doc: dict) -> dict[str, Any]:
    m = doc.get("metrics") or {}
    n = m.get("ask_n") or 0
    n_pass = m.get("ask_n_pass")
    stages_raw = (doc.get("cases") or {}).get("ask_latency_stages") or {}
    stages = []
    for key, label in STAGE_CN.items():
        st = stages_raw.get(key) or {}
        nn = int(st.get("n") or 0)
        stages.append({
            "key": key,
            "label": label,
            "n": nn,
            "p50_s": fmt_sec_ms(st.get("p50")) if nn else "—",
            "p95_s": fmt_sec_ms(st.get("p95")) if nn else "—",
            "p99_s": fmt_sec_ms(st.get("p99")) if nn else "—",
            "has_data": nn > 0,
        })

    ask_cases = [c for c in ((doc.get("cases") or {}).get("ask") or []) if isinstance(c, dict)]
    fails = []
    for c in ask_cases:
        if c.get("pass"):
            continue
        fails.append({
            "id": c.get("id") or "?",
            "layer": c.get("failure_layer") or "unknown",
            "latency_s": fmt_sec_ms(c.get("latency_ms")),
            "tokens": c.get("tokens"),
        })
    slow = sorted(
        [c for c in ask_cases if c.get("latency_ms") is not None],
        key=lambda x: -float(x.get("latency_ms") or 0),
    )[:5]
    slow_rows = [{
        "id": c.get("id") or "?",
        "pass": bool(c.get("pass")),
        "latency_s": fmt_sec_ms(c.get("latency_ms")),
        "retrieve_s": fmt_sec_ms((c.get("latency_stages_ms") or {}).get("retrieve_ms")),
        "tokens": c.get("tokens"),
    } for c in slow]

    return {
        "e2e_pass_rate": m.get("ask_e2e_pass_rate"),
        "e2e_frac": f"{n_pass}/{n}" if n_pass is not None and n else None,
        "grounding": m.get("ask_grounding"),
        "grounding_s": fmt_pct(m.get("ask_grounding"), digits=0),
        "precision_proxy": m.get("ask_precision_proxy"),
        "p50_ms": m.get("ask_p50_ms"),
        "p95_ms": m.get("ask_p95_ms"),
        "p99_ms": m.get("ask_p99_ms"),
        "p50_s": fmt_sec_ms(m.get("ask_p50_ms")),
        "p95_s": fmt_sec_ms(m.get("ask_p95_ms")),
        "p99_s": fmt_sec_ms(m.get("ask_p99_ms")),
        "token_total": m.get("ask_token_total"),
        "token_s": fmt_int(m.get("ask_token_total")),
        "retry_rate": m.get("retry_rate"),
        "retry_s": fmt_pct(m.get("retry_rate"), digits=2) if m.get("retry_rate") is not None else "无数据",
        "latency_sources": m.get("ask_latency_sources"),
        "stages": stages,
        "fail_cases": fails,
        "slow_cases": slow_rows,
    }


def _ledger(doc: dict) -> list[dict]:
    rel = (doc.get("cases") or {}).get("relation") or {}
    rows = rel.get("candidate_ledger") or []
    return [r for r in rows if isinstance(r, dict)]


def _case_brief(c: dict) -> dict[str, Any]:
    reason = (
        c.get("gate_reason_detail")
        or c.get("gate_reason")
        or c.get("llm_reason")
        or c.get("outcome_label")
        or ""
    )
    if isinstance(reason, str) and len(reason) > 160:
        reason = reason[:157] + "…"
    return {
        "id": c.get("candidate_id") or "",
        "title": c.get("candidate_title") or c.get("published_title") or "(无标题)",
        "outcome": c.get("outcome_label") or c.get("final_outcome") or "",
        "skip_code": c.get("skip_reason_code") or c.get("gate_reason_code") or "",
        "skip_label": SKIP_CODE_CN.get(
            c.get("skip_reason_code") or c.get("gate_reason_code") or "",
            c.get("skip_reason_code") or c.get("gate_reason_code") or "",
        ),
        "tier": c.get("decision_tier"),
        "reason": reason,
        "needs_review": bool(c.get("needs_human_review")),
        "review_hint": c.get("review_hint") or "",
    }


def relation_board(doc: dict) -> dict[str, Any]:
    m = doc.get("metrics") or {}
    ledger = _ledger(doc)
    n_ledger_review = sum(1 for c in ledger if c.get("needs_human_review"))
    return {
        "keep_rate_proxy": m.get("rel_candidate_keep_rate_proxy"),
        "keep_s": fmt_pct(m.get("rel_candidate_keep_rate_proxy"), digits=0),
        "gate_accept_proxy": m.get("rel_decision_gate_accept_rate_proxy"),
        "gate_s": fmt_pct(m.get("rel_decision_gate_accept_rate_proxy"), digits=0),
        "grounding": m.get("rel_grounding"),
        "grounding_s": fmt_pct(m.get("rel_grounding"), digits=0),
        "human_review": m.get("human_review_count"),
        "human_review_ledger": n_ledger_review,
        "strong_no_evidence": m.get("rel_strong_no_evidence"),
        "blocked_leaks": m.get("rel_blocked_leaks"),
        "missing_tier": m.get("rel_missing_tier"),
        "is_proxy": True,
        "proxy_badge": "代理指标 Proxy",
        "proxy_disclaimer": (
            "带「代理」标记的数字是行为代理，不是金标召回率 / 精确率。"
            "保留率=0% 只说明这次漏斗草稿为 0，不能直接写成「关系系统 0 分、很差」。"
        ),
        "grounding_note": (
            "「关系完整性」= 库存体检合格卡 / 库存卡（n_ok / n_relations），"
            "和下面「关系漏斗」不是同一批 Preview 产物。"
        ),
        "human_review_note": (
            "口径：草稿积压(n_draft_backlog) + 缺 decision_tier。"
            f"当前值 {m.get('human_review_count')}；"
            f"漏斗 ledger 里 needs_human_review={n_ledger_review}（另一口径，勿混用）。"
        ),
    }


def relation_funnel(doc: dict) -> dict[str, Any]:
    """Decision audit funnel only — never mix with store integrity counts."""
    rel = (doc.get("cases") or {}).get("relation") or {}
    audit = rel.get("audit_summary") or {}
    m = doc.get("metrics") or {}
    ledger = _ledger(doc)

    n_cand = int(audit.get("n_candidates") or m.get("n_candidates_for_decision") or 0)
    n_skip_llm = int(audit.get("n_skipped_llm") or 0)
    n_gate_override = int(audit.get("n_skipped_gate_override") or 0)
    n_draft = int(
        audit.get("n_draft_relations")
        if audit.get("n_draft_relations") is not None
        else (m.get("n_draft_relations") or 0)
    )
    n_reader = int(audit.get("n_reader_relations") or 0)
    n_llm_keep = max(0, n_cand - n_skip_llm) if n_cand else None
    if n_llm_keep is not None:
        n_gate_pass = max(0, n_llm_keep - n_gate_override)
    else:
        n_gate_pass = n_draft

    drop_hint = None
    if n_cand and n_draft == 0:
        if n_llm_keep and n_gate_pass == 0:
            drop_hint = f"主要掉在门控：模型想留 {n_llm_keep} 条，门控拦掉 {n_gate_override} 条，草稿为 0。"
        elif n_llm_keep == 0:
            drop_hint = f"主要掉在决策：{n_cand} 个候选里模型跳过了 {n_skip_llm} 个，没有进入门控。"
        else:
            drop_hint = "候选有量，但最终草稿为 0；请对照各层数字看掉在哪。"

    def _pick(pred) -> list[dict]:
        return [_case_brief(c) for c in ledger if pred(c)]

    cases_by_step = {
        "candidates": _pick(lambda c: True),
        "decision": _pick(lambda c: c.get("decision_candidate") is not False),
        "keep": _pick(lambda c: bool(c.get("decision_keep") or c.get("llm_decision") == "keep")),
        "gate": _pick(lambda c: bool(c.get("gate_keep") or (c.get("draft_kept") and not c.get("gate_skip")))),
        "draft": _pick(lambda c: bool(c.get("draft_kept"))),
        "reader": _pick(lambda c: bool(c.get("reader_visible"))),
        "decision_drop": _pick(lambda c: bool(c.get("decision_skip") or c.get("final_outcome") == "skipped_llm")),
        "gate_drop": _pick(lambda c: bool(c.get("gate_overrode_llm") or c.get("final_outcome") == "skipped_gate_override")),
    }

    skip_rows = []
    for code, cnt in (audit.get("by_skip_code") or {}).items():
        rows = _pick(lambda c, code=code: (c.get("skip_reason_code") or c.get("gate_reason_code")) == code)
        case_key = f"skip:{code}"
        cases_by_step[case_key] = rows
        skip_rows.append({
            "code": code,
            "label": SKIP_CODE_CN.get(code, code),
            "n": cnt,
            "case_key": case_key,
        })
    skip_rows.sort(key=lambda r: -int(r["n"] or 0))

    # 每层直接可见的原因摘要（标题 + 原因码）
    reason_preview = []
    for c in cases_by_step["gate_drop"][:5] + cases_by_step["decision_drop"][:3]:
        reason_preview.append({
            "title": c["title"],
            "label": c["skip_label"] or c["outcome"],
            "reason": c["reason"],
            "skip_code": c.get("skip_code") or "",
        })

    steps = [
        {"id": "candidates", "label": "候选", "hint": "进入决策的关系候选", "n": n_cand or None, "case_key": "candidates"},
        {
            "id": "decision",
            "label": "进决策",
            "hint": "交给模型判断",
            "n": n_cand or None,
            "detail": f"模型跳过 {n_skip_llm}",
            "case_key": "decision",
            "drop_key": "decision_drop",
        },
        {
            "id": "keep",
            "label": "模型想留",
            "hint": "LLM Keep",
            "n": n_llm_keep,
            "detail": f"随后被门控拦 {n_gate_override}",
            "case_key": "keep",
        },
        {
            "id": "gate",
            "label": "门控通过",
            "hint": "事实约束 Gate",
            "n": n_gate_pass,
            "detail": f"拦截 {n_gate_override}" if n_gate_override else None,
            "case_key": "gate",
            "drop_key": "gate_drop",
        },
        {"id": "draft", "label": "进草稿", "hint": "写入 draft", "n": n_draft, "case_key": "draft"},
        {"id": "reader", "label": "读者可见", "hint": "仅 strong 档", "n": n_reader, "case_key": "reader"},
    ]

    return {
        "source": "decision_audit / candidate_ledger",
        "not_store": True,
        "drop_hint": drop_hint,
        "steps": steps,
        "by_outcome": audit.get("by_outcome") or {},
        "by_skip_code": audit.get("by_skip_code") or {},
        "skip_rows": skip_rows,
        "reason_preview": reason_preview,
        "cases_by_step": cases_by_step,
        "click_hint": "点漏斗节点或拒绝原因，直接落到 cases。",
    }


def relation_integrity(doc: dict) -> dict[str, Any]:
    """Store integrity — separate panel from funnel."""
    rel = (doc.get("cases") or {}).get("relation") or {}
    integ = rel.get("integrity") or {}
    m = doc.get("metrics") or {}
    n_rel = integ.get("n_relations")
    if n_rel is None:
        n_rel = m.get("n_relations_scanned")
    n_ok = integ.get("n_ok")
    if n_ok is None:
        n_ok = m.get("n_relations_grounded_ok")
    return {
        "source": "store integrity scan",
        "n_relations": n_rel,
        "n_ok": n_ok,
        "grounding": m.get("rel_grounding"),
        "grounding_s": fmt_pct(m.get("rel_grounding"), digits=0),
        "strong_no_evidence": integ.get("strong_no_evidence", m.get("rel_strong_no_evidence")),
        "blocked_leaks": integ.get("blocked_leaks", m.get("rel_blocked_leaks")),
        "team_mismatch": integ.get("team_mismatch", m.get("rel_team_mismatch")),
        "missing_tier": integ.get("missing_tier", m.get("rel_missing_tier")),
        "n_draft_store": m.get("n_draft_relations_store"),
        "title": "关系完整性（库存体检）",
        "disclaimer": (
            "这是库存里现有关系卡的体检，不是上面漏斗那一次决策流水。"
            f"例如库存可能有 {n_rel or '—'} 张卡，但本次漏斗草稿可以是 0 —— 两套数字不要混成一条漏斗。"
        ),
    }


def env_card(doc: dict) -> dict[str, Any]:
    env = doc.get("env") or {}
    ph = env.get("prompt_hashes") or {}
    keys = [
        "issue_relation_decisions.md",
        "issue_relation_writer.md",
        "issue_draft.md",
        "qa.md",
    ]
    prompt_focus = {k: ph.get(k) for k in keys if ph.get(k)}
    analysis = env.get("analysis") or {}
    return {
        "commit": env.get("commit"),
        "commit_short": (env.get("commit") or "")[:7] or None,
        "git_dirty": env.get("git_dirty"),
        "git_untracked_present": env.get("git_untracked_present"),
        "branch": env.get("branch"),
        "corpus": doc.get("corpus"),
        "slug": doc.get("slug"),
        "ask_eval_set": doc.get("ask_eval_set"),
        "provider": env.get("provider"),
        "llm_model": env.get("llm_model"),
        "embedding_model": env.get("embedding_model"),
        "analysis": analysis,
        "analysis_ask": analysis.get("MESH_ASK_ANALYSIS"),
        "analysis_max_groups": analysis.get("MESH_ANALYSIS_MAX_GROUPS"),
        "prompt_hashes_focus": prompt_focus,
        "prompt_hash_count": len(ph),
        "eval_dataset_hash": env.get("eval_dataset_hash"),
        "topk": env.get("topk"),
        "rerank": env.get("rerank"),
        "frozen_at": doc.get("frozen_at"),
        "schema": doc.get("schema"),
        "file": doc.get("_file"),
        "label": doc.get("baseline"),
    }


def compare_delta(base: dict, current: dict, *, funnel: dict | None = None) -> list[dict[str, Any]]:
    try:
        from deploy.mesh_baseline import compare
    except ImportError:
        import sys

        mesh_root = str(ROOT)
        if mesh_root not in sys.path:
            sys.path.insert(0, mesh_root)
        from deploy.mesh_baseline import compare

    rows, _blocked = compare(base, current)
    out = []
    for r in rows:
        key = r.get("key") or ""
        st = r.get("status") or ""
        evidence = _delta_evidence(key, current, funnel or {})
        out.append({
            **r,
            "metric_cn": METRIC_CN.get(key, r.get("metric") or key),
            "status_cn": STATUS_CN.get(st, st),
            "baseline_s": fmt_metric(key, r.get("baseline")),
            "new_s": fmt_metric(key, r.get("new")),
            "delta_display": fmt_delta(key, r.get("delta"), r.get("delta_s")),
            "why": _why(r),
            "evidence": evidence,
        })
    return out


def _delta_evidence(key: str, current: dict, funnel: dict) -> list[dict[str, str]]:
    """给 Δ 行挂上可点的具体 case，方便 2 分钟定位。"""
    ask_cases = [c for c in ((current.get("cases") or {}).get("ask") or []) if isinstance(c, dict)]
    by_step = funnel.get("cases_by_step") or {}

    if key == "ask_e2e_pass_rate":
        return [{
            "id": str(c.get("id") or "?"),
            "detail": f"失败层={c.get('failure_layer') or '?'} · {fmt_sec_ms(c.get('latency_ms'))}",
        } for c in ask_cases if not c.get("pass")][:8]

    if key in MS_KEYS:
        slow = sorted(
            [c for c in ask_cases if c.get("latency_ms") is not None],
            key=lambda x: -float(x.get("latency_ms") or 0),
        )[:5]
        return [{
            "id": str(c.get("id") or "?"),
            "detail": f"{fmt_sec_ms(c.get('latency_ms'))} · retrieve {fmt_sec_ms((c.get('latency_stages_ms') or {}).get('retrieve_ms'))}",
        } for c in slow]

    if key == "ask_token_total":
        heavy = sorted(
            [c for c in ask_cases if c.get("tokens")],
            key=lambda x: -int(x.get("tokens") or 0),
        )[:5]
        return [{"id": str(c.get("id") or "?"), "detail": f"tokens={c.get('tokens')}"} for c in heavy]

    if key.startswith("rel_") and "proxy" in key:
        rows = by_step.get("gate_drop") or by_step.get("decision_drop") or []
        return [{"id": c.get("title") or c.get("id") or "?", "detail": c.get("skip_label") or c.get("reason") or ""} for c in rows[:6]]

    if key in ("rel_grounding", "rel_strong_no_evidence", "rel_missing_tier", "rel_blocked_leaks"):
        integ = ((current.get("cases") or {}).get("relation") or {}).get("integrity") or {}
        bad = integ.get("bad_titles") or []
        return [{"id": t, "detail": "库存体检 bad_title"} for t in bad[:8]]

    return []


def _why(row: dict) -> str:
    key = row.get("key") or ""
    st = row.get("status") or ""
    if st in ("-", "[.]"):
        return "和基线比没有变化，或两边缺数不可比。"
    if key in ("ask_grounding", "rel_grounding") and st == "[BLOCKER]":
        return "有据率 / 关系完整性相对基线下降，属于发布阻断项。"
    if key.startswith("rel_") and "proxy" in key:
        return "这是行为代理指标，不是金标。请看关系漏斗掉在哪一层。"
    if key in ("ask_p50_ms", "ask_p95_ms", "ask_p99_ms"):
        return "整题端到端耗时；阶段耗时另看 cases，不在这张总表里。"
    if key == "ask_token_total":
        return "Ask 用量合计；直答路径可能没有 token。"
    if key == "retry_rate":
        return "只有报告里记录了重试次数才有值；空值不等于 0。"
    if key == "ask_e2e_pass_rate":
        return "评测题通过数 / 总数，不是检索召回率。"
    if st == "[WARN]":
        return "可接受的交易型回退：合并前需写明「我接受 … 因为 …」。"
    if st == "[OK]":
        return "相对基线变好，或硬底线指标持平。"
    return ""


def _conclusion(ask: dict, boundary: dict, funnel: dict, delta: list[dict], relation: dict) -> dict[str, Any]:
    """顶部一句当前结论 + 三问拆解。"""
    ask_frac = ask.get("e2e_frac") or "—"
    drop = funnel.get("drop_hint") or "关系漏斗暂无明显掉量说明。"
    blockers = [r for r in delta if r.get("status") == "[BLOCKER]"]
    warns = [r for r in delta if r.get("status") == "[WARN]"]

    def _moved(row: dict) -> bool:
        d = row.get("delta")
        if d is None:
            return False
        try:
            return abs(float(d)) > 1e-9
        except (TypeError, ValueError):
            return False

    oks = [r for r in delta if r.get("status") == "[OK]" and _moved(r)]

    if blockers:
        delta_bit = f"相对基线有 {len(blockers)} 项阻断"
        tone = "block"
    elif warns:
        delta_bit = f"相对基线无阻断、有 {len(warns)} 项可交易回退"
        tone = "warn"
    elif oks:
        delta_bit = f"相对基线 {len(oks)} 项变好、其余持平"
        tone = "ok"
    else:
        delta_bit = "相对基线持平（Δ≈0）"
        tone = "same"

    keep = relation.get("keep_s") or "—"
    integrity = relation.get("grounding_s") or "—"
    verdict = (
        f"Ask {ask_frac} 可用、有据 {ask.get('grounding_s') or '—'}；"
        f"关系代理保留率 {keep}（≠质量分），关系完整性 {integrity}；"
        f"{drop.rstrip('。')}；{delta_bit}；边界 {boundary['fails']} 失败 / {boundary['warns']} 警告。"
    )

    q1 = f"Ask {ask_frac} 通过，有据率 {ask.get('grounding_s') or '—'}；边界 {boundary['fails']}/{boundary['warns']}/{boundary['passes']}。"
    q2 = drop
    q3 = delta_bit + "。"

    return {
        "verdict": verdict,
        "tone": tone,
        "quality": q1,
        "relation_drop": q2,
        "delta": q3,
    }


def _enrich_experiments(exps: list[dict], baseline_file: str | None, current_file: str | None) -> list[dict]:
    """Attach one-line Δ vs selected baseline for history list."""
    base = load_experiment(baseline_file) if baseline_file else None
    out = []
    for ex in exps:
        row = dict(ex)
        row["is_selected"] = ex.get("name") == current_file
        row["is_baseline"] = ex.get("name") == baseline_file
        if base and ex.get("ok"):
            cur = load_experiment(ex["name"])
            if cur:
                delta = compare_delta(base, cur)
                blockers = sum(1 for r in delta if r["status"] == "[BLOCKER]")
                warns = sum(1 for r in delta if r["status"] == "[WARN]")
                oks = sum(
                    1
                    for r in delta
                    if r["status"] == "[OK]"
                    and r.get("delta") is not None
                    and abs(float(r["delta"])) > 1e-9
                )
                if blockers:
                    row["vs_baseline"] = f"vs 基线：{blockers} 阻断 / {warns} 回退 / {oks} 变好"
                elif warns:
                    row["vs_baseline"] = f"vs 基线：{warns} 回退 / {oks} 变好"
                elif oks:
                    row["vs_baseline"] = f"vs 基线：{oks} 项变好，其余持平"
                else:
                    row["vs_baseline"] = "vs 基线：持平"
            else:
                row["vs_baseline"] = None
        else:
            row["vs_baseline"] = None
        out.append(row)
    return out


def build_view(
    *,
    current_name: str | None = None,
    baseline_name: str | None = None,
    role: str | None = None,
) -> dict[str, Any]:
    """Assemble read-only dashboard payload."""
    roles = resolve_role_files()
    active_role = role if role in ("baseline", "latest", "production") else None
    if active_role and not current_name:
        current_name = (roles.get(active_role) or {}).get("file")

    current = load_experiment(current_name or CURRENT_POINTER)
    if not current:
        return {"ok": False, "error": "未找到 Baseline JSON（eval/reports/BASELINE_*.json）"}

    if not baseline_name:
        baseline_name = (roles.get("baseline") or {}).get("file") or CURRENT_POINTER
    baseline = load_experiment(baseline_name) or current

    # infer role from filename if not provided
    if not active_role:
        cur_file = current.get("_file")
        for rname, meta in roles.items():
            if meta.get("file") == cur_file:
                active_role = rname
                break
        active_role = active_role or "latest"

    ask = ask_board(current)
    boundary = boundary_summary()
    funnel = relation_funnel(current)
    relation = relation_board(current)
    delta = compare_delta(baseline, current, funnel=funnel)
    headline = _conclusion(ask, boundary, funnel, delta, relation)
    experiments = _enrich_experiments(list_experiments(), baseline.get("_file"), current.get("_file"))

    return {
        "ok": True,
        "readonly": True,
        "readonly_note": "目标：30 秒发现问题，2 分钟定位到具体 case。只读报告，不触发业务任务。",
        "headline": headline,
        "roles": roles,
        "active_role": active_role,
        "boundary": boundary,
        "env": env_card(current),
        "ask": ask,
        "relation": relation,
        "funnel": funnel,
        "integrity": relation_integrity(current),
        "delta": delta,
        "baseline_file": baseline.get("_file"),
        "current_file": current.get("_file"),
        "experiments": experiments,
        "notes_proxy": (current.get("notes") or {}).get("proxy") or [],
        "cases_json": json.dumps(funnel.get("cases_by_step") or {}, ensure_ascii=False),
    }
