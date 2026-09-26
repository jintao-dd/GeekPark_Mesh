#!/usr/bin/env python3
"""Agent Quality v2.4 · Fixed-model LLM-on Baseline（只测不修）。

冻结：Ranking v1.4 · Recall · Vector OFF · Contract · claim_support v2.3e（不改）
变量：仅 MESH_AGENT_USE_LLM=1 + 环境固定模型

目标：回答三问——
  1) LLM path 比 deterministic 多解决/多引入什么
  2) Temporal T03/T19/T20 是否路径差异
  3) unseen claim-strength 是规则泛化不足还是模型判断不足

用法：
  PYTHONPATH=. python eval/run_quality_v2_4_llm_on.py --reuse-env-db
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"


def _py(args: list[str], env: dict | None = None) -> int:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT)
    e["MESH_AGENT_USE_LLM"] = "1"
    e["MESH_RECALL_USE_EMBED"] = "0"
    e["MESH_RANKING_QUALITY"] = e.get("MESH_RANKING_QUALITY", "1")
    if env:
        e.update(env)
    print(">>", " ".join(args), "MESH_AGENT_USE_LLM=1", flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _model_version(con) -> str:
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key IN ('llm_model','LLM_MODEL','mesh_llm_model') LIMIT 1"
        ).fetchone()
        if row:
            return str(row[0] or "")[:120]
    except Exception:
        pass
    return (
        os.environ.get("MESH_LLM_MODEL")
        or os.environ.get("LLM_MODEL")
        or "unset"
    )


def _fail_ids(report: dict, *, results_key: str = "results") -> list[str]:
    out = []
    for r in report.get(results_key) or []:
        m = r.get("metrics") or {}
        if m.get("pass") is False or (r.get("pass") is False):
            out.append(r.get("id") or "?")
        elif "pass" in r and not r["pass"]:
            out.append(r.get("id") or "?")
    return out


def _delta(det_fails: list[str], llm_fails: list[str]) -> dict:
    ds, ls = set(det_fails), set(llm_fails)
    return {
        "fixed_by_llm": sorted(ds - ls),
        "new_fails_under_llm": sorted(ls - ds),
        "still_fail": sorted(ds & ls),
        "still_pass": "n/a",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-temporal", action="store_true")
    args = ap.parse_args()

    os.environ["MESH_AGENT_USE_LLM"] = "1"
    os.environ["MESH_RECALL_USE_EMBED"] = "0"

    from app import db

    con = db.connect()
    try:
        model = _model_version(con)
    finally:
        con.close()

    det = _load(REPORTS / "baselines" / "QUALITY_V2_3E_CANONICAL.json")
    det_unseen = _load(REPORTS / "UNSEEN_V23E_ACCEPTANCE.json")

    t0 = time.time()
    steps: list[dict] = []

    if not args.skip_temporal:
        steps.append({"step": "temporal_llm_on", "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])})

    steps.append(
        {
            "step": "answer_canonical_llm",
            "rc": _py(
                [
                    "eval/run_answer_v2.py",
                    "--reuse-env-db",
                    "--tag",
                    "answer_llm_v24",
                    "--gold",
                    "eval/answer_gold_v2.jsonl",
                ]
            ),
        }
    )
    steps.append(
        {
            "step": "evidence_canonical_llm",
            "rc": _py(
                [
                    "eval/run_evidence_baseline.py",
                    "--reuse-env-db",
                    "--tag",
                    "evidence_llm_v24",
                    "--gold",
                    "eval/evidence_gold_v2.jsonl",
                ]
            ),
        }
    )
    steps.append(
        {
            "step": "unseen_llm",
            "rc": _py(
                [
                    "eval/run_answer_evidence_acceptance.py",
                    "--reuse-env-db",
                    "--tag",
                    "unseen_llm_v24",
                    "--answer-gold",
                    "eval/answer_gold_unseen_v23e.jsonl",
                    "--evidence-gold",
                    "eval/evidence_gold_unseen_v23e.jsonl",
                ]
            ),
        }
    )

    temporal = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    answer = _load(REPORTS / "experiments" / "answer_llm_v24" / "ANSWER_answer_llm_v24.json")
    if not answer:
        answer = _load(REPORTS / "ANSWER_V2_latest.json")
    evidence = _load(REPORTS / "experiments" / "evidence_llm_v24" / "EVIDENCE_evidence_llm_v24.json")
    if not evidence:
        # tag v2 style
        evidence = _load(REPORTS / "experiments" / "evidence_llm_v24" / "EVIDENCE_evidence_llm_v24.json")
        evidence = evidence or _load(REPORTS / "EVIDENCE_evidence_v2.json")
    # evidence runner writes experiments/<tag>/EVIDENCE_<tag>.json
    for cand in (
        REPORTS / "experiments" / "evidence_llm_v24" / "EVIDENCE_evidence_llm_v24.json",
        REPORTS / "EVIDENCE_evidence_llm_v24_latest.json",
        REPORTS / "experiments" / "v2" / "EVIDENCE_v2.json",
    ):
        if cand.exists():
            evidence = _load(cand)
            break

    unseen = _load(REPORTS / "experiments" / "unseen_llm_v24" / "ACCEPT_unseen_llm_v24.json")
    if not unseen:
        unseen = _load(REPORTS / "ANSWER_EVIDENCE_ACCEPTANCE_latest.json")

    ans_fails = _fail_ids(answer)
    ev_fails = _fail_ids(evidence)
    t_fails = [r["id"] for r in (temporal.get("results") or []) if not r.get("pass")]
    u_ans_fails = [
        x["id"] if isinstance(x, dict) else x
        for x in ((unseen.get("remaining_for_triage") or {}).get("answer_fails") or unseen.get("answer_fails") or [])
    ]
    if not u_ans_fails and unseen.get("answer_results"):
        u_ans_fails = [r["id"] for r in unseen["answer_results"] if not r["metrics"]["pass"]]
    u_ev_fails = [
        x["id"] if isinstance(x, dict) else x
        for x in ((unseen.get("remaining_for_triage") or {}).get("evidence_fails") or unseen.get("evidence_fails") or [])
    ]
    if not u_ev_fails and unseen.get("evidence_results"):
        u_ev_fails = [r["id"] for r in unseen["evidence_results"] if not r["metrics"]["pass"]]

    # deterministic baselines
    det_ans_fails = (det.get("answer") or {}).get("fails") or []
    det_ev_fails = (det.get("evidence") or {}).get("fails") or []
    det_t = det.get("temporal") or {}
    det_t_fails = ["T03", "T19", "T20"] if (det_t.get("pass_rate") or 1) < 0.99 else []
    # from prior report if temporal was 0.875
    if det_t.get("pass_rate") == 0.875:
        det_t_fails = ["T03", "T19", "T20"]

    det_u_ans = [
        x["id"] if isinstance(x, dict) else x
        for x in ((det_unseen.get("remaining_for_triage") or {}).get("answer_fails") or [])
    ]
    if not det_u_ans and det_unseen.get("answer_results"):
        det_u_ans = [r["id"] for r in det_unseen["answer_results"] if not r["metrics"]["pass"]]
    det_u_ev = [
        x["id"] if isinstance(x, dict) else x
        for x in ((det_unseen.get("remaining_for_triage") or {}).get("evidence_fails") or [])
    ]
    if not det_u_ev and det_unseen.get("evidence_results"):
        det_u_ev = [r["id"] for r in det_unseen["evidence_results"] if not r["metrics"]["pass"]]

    llm_used_n = sum(1 for r in (answer.get("results") or []) if r.get("llm_used"))
    # answer_v2 may not record llm_used — check acceptance unseen
    if unseen.get("answer_results"):
        llm_used_n = sum(1 for r in unseen["answer_results"] if r.get("llm_used")) + sum(
            1 for r in (answer.get("results") or []) if r.get("llm_used")
        )

    # enrich answer results with llm_used from acceptance if needed — grade via re-read out
    # Unseen support matrix
    unseen_matrix = []
    for r in unseen.get("answer_results") or []:
        cs = r.get("claim_support") or {}
        unseen_matrix.append(
            {
                "id": r["id"],
                "pass": r["metrics"]["pass"],
                "llm_used": r.get("llm_used"),
                "support": cs.get("support"),
                "claim_strength": cs.get("claim_strength"),
                "fail_class": r.get("fail_class") or r["metrics"].get("fail_class"),
            }
        )
    for r in unseen.get("evidence_results") or []:
        cs = r.get("claim_support") or {}
        unseen_matrix.append(
            {
                "id": r["id"],
                "pass": r["metrics"]["pass"],
                "llm_used": r.get("llm_used"),
                "support": cs.get("support"),
                "expect": r.get("expect_support"),
                "fail_class": r.get("fail_class") or r["metrics"].get("fail_class"),
            }
        )

    report = {
        "phase": "quality_v2_4_llm_on",
        "elapsed_s": round(time.time() - t0, 2),
        "baseline": {
            "mesh_agent_use_llm": "1",
            "model_version": model,
            "ranking_version": "v1.4",
            "vector": "OFF",
            "agent_contract": "frozen_v1",
            "claim_support": "v2.3e_frozen_no_edits",
            "prompt_version": "ask_engine+temporal_hard_rules+claim_support+llm.answer_question",
            "note": "measure only; no claim_support / Ranking / Ontology edits",
        },
        "steps": steps,
        "temporal": {
            "n": temporal.get("n"),
            "pass_rate": temporal.get("pass_rate"),
            "fails": t_fails,
            "vs_det_0_875": _delta(det_t_fails, t_fails),
        },
        "answer_canonical": {
            "n": answer.get("n"),
            "pass_rate": answer.get("pass_rate"),
            "fails": ans_fails,
            "vs_det": _delta(det_ans_fails, ans_fails),
        },
        "evidence_canonical": {
            "n": evidence.get("n"),
            "pass_rate": evidence.get("pass_rate"),
            "fails": ev_fails,
            "vs_det": _delta(det_ev_fails, ev_fails),
        },
        "unseen": {
            "answer_pass_rate": (unseen.get("answer") or {}).get("pass_rate"),
            "evidence_pass_rate": (unseen.get("evidence") or {}).get("pass_rate"),
            "answer_fails": u_ans_fails,
            "evidence_fails": u_ev_fails,
            "vs_det_answer": _delta(det_u_ans, u_ans_fails),
            "vs_det_evidence": _delta(det_u_ev, u_ev_fails),
            "matrix": unseen_matrix,
        },
        "three_questions": {
            "q1_llm_vs_det": {
                "answer_delta": _delta(det_ans_fails, ans_fails),
                "evidence_delta": _delta(det_ev_fails, ev_fails),
                "unseen_answer_delta": _delta(det_u_ans, u_ans_fails),
                "unseen_evidence_delta": _delta(det_u_ev, u_ev_fails),
            },
            "q2_temporal_path": {
                "det_fails_reported": det_t_fails,
                "llm_on_fails": t_fails,
                "delta": _delta(det_t_fails, t_fails),
                "interpretation_hint": (
                    "same_fails→likely real temporal/LLM issue; "
                    "cleared→prior 0.875 was path noise; "
                    "new_fails→LLM path regresses temporal"
                ),
            },
            "q3_unseen_strength": {
                "det_answer_pass": (det_unseen.get("answer") or {}).get("pass_rate"),
                "llm_answer_pass": (unseen.get("answer") or {}).get("pass_rate"),
                "det_evidence_pass": (det_unseen.get("evidence") or {}).get("pass_rate"),
                "llm_evidence_pass": (unseen.get("evidence") or {}).get("pass_rate"),
                "interpretation_hint": (
                    "if LLM clears unseen→model judgment helps; "
                    "if still false-support with claim_strength=weak→rule generalization gap; "
                    "if over-abstain on E28→LLM too cautious"
                ),
            },
        },
        "verdict": {
            "optimized": False,
            "claim_support_edited": False,
            "next": "analyze deltas → decide A rule / B LLM judge / C hybrid; then Model A/B",
        },
    }

    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_V2_4_LLM_ON.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = REPORTS / "AGENT_QUALITY_V2_4_LLM_ON_BASELINE.md"
    md_path.write_text(_md(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in ("steps",)}, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", md_path)
    return 0


def _md(report: dict) -> str:
    b = report["baseline"]
    t = report["temporal"]
    a = report["answer_canonical"]
    e = report["evidence_canonical"]
    u = report["unseen"]
    q = report["three_questions"]
    return f"""# Agent Quality v2.4 · Fixed-model LLM-on Baseline

> **只测不修。** claim_support / Ranking / Ontology / Contract：**不动**。

## Baseline lock

| 字段 | 值 |
|------|-----|
| MESH_AGENT_USE_LLM | **1** |
| model_version | `{b.get('model_version')}` |
| ranking | v1.4 |
| vector | OFF |
| claim_support | v2.3e frozen |
| prompt | {b.get('prompt_version')} |

elapsed_s={report.get('elapsed_s')}

## Results vs deterministic v2.3e

| Layer | LLM-on | vs det |
|-------|--------|--------|
| Temporal | pass={t.get('pass_rate')} fails={t.get('fails')} | {t.get('vs_det_0_875')} |
| Answer canonical | pass={a.get('pass_rate')} fails={a.get('fails')} | {a.get('vs_det')} |
| Evidence canonical | pass={e.get('pass_rate')} fails={e.get('fails')} | {e.get('vs_det')} |
| Unseen Answer | pass={u.get('answer_pass_rate')} fails={u.get('answer_fails')} | {u.get('vs_det_answer')} |
| Unseen Evidence | pass={u.get('evidence_pass_rate')} fails={u.get('evidence_fails')} | {u.get('vs_det_evidence')} |

## Three questions

### Q1 · LLM path vs deterministic
```json
{json.dumps(q['q1_llm_vs_det'], ensure_ascii=False, indent=2)}
```

### Q2 · Temporal T03/T19/T20
```json
{json.dumps(q['q2_temporal_path'], ensure_ascii=False, indent=2)}
```

### Q3 · Unseen claim-strength
```json
{json.dumps(q['q3_unseen_strength'], ensure_ascii=False, indent=2)}
```

Unseen matrix:
```json
{json.dumps(u.get('matrix'), ensure_ascii=False, indent=2)}
```

## Next（未执行）

分析后再选：A deterministic rule 扩展 · B LLM semantic judge · C 组合 · 然后 Model A/B。  
**禁止**本轮为刷分改 claim_support。
"""


if __name__ == "__main__":
    raise SystemExit(main())
