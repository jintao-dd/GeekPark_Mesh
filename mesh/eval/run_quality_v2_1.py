#!/usr/bin/env python3
"""Agent Quality v2.1 · Unified Runner

顺序：Temporal → Retrieval → Ranking(baseline+v1.1) → Evidence v2 → Answer v2
Vector 默认 OFF。禁止覆盖 frozen baselines。

用法：
  PYTHONPATH=. python eval/run_quality_v2_1.py --reuse-env-db
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
    if env:
        e.update(env)
    print(">>", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _rk(d: dict) -> dict:
    return {
        "mrr": d.get("macro_mrr"),
        "ndcg@10": d.get("macro_ndcg@10"),
        "precision@5": d.get("macro_precision@5"),
        "post_rank_R@5": d.get("macro_recall@5"),
        "post_rank_R@10": d.get("macro_recall@10"),
        "post_rank_R@20": d.get("macro_recall@20"),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-temporal", action="store_true")
    ap.add_argument("--ids-ranking", default="", help="optional ranking subset e.g. R06,R13,R18")
    args = ap.parse_args()

    t0 = time.time()
    steps: list[dict] = []

    if not args.skip_temporal:
        rc = _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])
        steps.append({"step": "temporal", "rc": rc})

    rc = _py(["eval/run_recall_baseline.py", "--reuse-env-db"], {"MESH_RECALL_USE_EMBED": "0"})
    steps.append({"step": "recall_fts", "rc": rc})

    rc = _py(["eval/run_ranking_baseline.py", "--reuse-env-db", "--profile", "baseline"])
    steps.append({"step": "ranking_baseline", "rc": rc})

    rank_cmd = [
        "eval/run_ranking_baseline.py",
        "--reuse-env-db",
        "--profile",
        "v1_1",
        "--tag",
        "ranking_v1_1",
    ]
    if args.ids_ranking.strip():
        rank_cmd.extend(["--ids", args.ids_ranking.strip()])
    rc = _py(rank_cmd)
    steps.append({"step": "ranking_v1_1", "rc": rc})

    rc = _py(["eval/run_evidence_baseline.py", "--reuse-env-db", "--tag", "evidence_v2"])
    steps.append({"step": "evidence_v2", "rc": rc})

    rc = _py(["eval/run_answer_v2.py", "--reuse-env-db", "--tag", "answer_v2"])
    steps.append({"step": "answer_v2", "rc": rc})

    temporal = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    recall = _load(REPORTS / "RECALL_BASELINE_latest.json")
    recall_frozen = _load(REPORTS / "baselines" / "RECALL_BASELINE_v1.json") or recall
    ranking_b = _load(REPORTS / "RANKING_BASELINE_latest.json")
    ranking_v11 = _load(REPORTS / "experiments" / "ranking_v1_1" / "RANKING_ranking_v1_1_latest.json")
    evidence = _load(REPORTS / "EVIDENCE_evidence_v2_latest.json")
    if not evidence:
        evidence = _load(REPORTS / "experiments" / "evidence_v2" / "EVIDENCE_evidence_v2.json")
    answer = _load(REPORTS / "ANSWER_V2_latest.json")

    rb, rv = _rk(ranking_b), _rk(ranking_v11)
    delta = {
        k: round(float(rv.get(k) or 0) - float(rb.get(k) or 0), 4)
        for k in ("mrr", "ndcg@10", "precision@5")
    }

    # R06/R13/R18 regression check vs ranking baseline results
    regressions = []
    improvements = []
    if ranking_b.get("results") and ranking_v11.get("results"):
        bm = {r["id"]: r for r in ranking_b["results"]}
        vm = {r["id"]: r for r in ranking_v11["results"]}
        for qid in ("R06", "R13", "R18"):
            if qid not in bm or qid not in vm:
                continue
            bpat, vpat = bm[qid].get("failure_pattern"), vm[qid].get("failure_pattern")
            bnd, vnd = bm[qid]["metrics"]["ndcg@10"], vm[qid]["metrics"]["ndcg@10"]
            if bpat == "ok" and vpat != "ok":
                regressions.append({"id": qid, "from": bpat, "to": vpat, "ndcg_delta": round(vnd - bnd, 4)})
            elif vnd + 1e-9 >= bnd and (vpat == "ok" or vnd >= bnd):
                improvements.append({"id": qid, "from": bpat, "to": vpat, "ndcg_delta": round(vnd - bnd, 4)})
            elif vnd < bnd - 0.01:
                regressions.append({"id": qid, "from": bpat, "to": vpat, "ndcg_delta": round(vnd - bnd, 4)})

    merge_ok = (
        (delta.get("mrr") or 0) >= -0.005
        and (delta.get("ndcg@10") or 0) >= -0.005
        and not any(r["id"] == "R06" and r.get("to") != "ok" for r in regressions)
        and not regressions
    )
    decision = (
        "CANDIDATE for production merge review"
        if merge_ok and ((delta.get("mrr") or 0) > 0.005 or (delta.get("ndcg@10") or 0) > 0.005)
        else "KEEP current production ranking; ranking_v1_1 remains experiment-only"
    )

    summary = {
        "phase": "Agent Quality v2.1",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "steps": steps,
        "metrics_policy": {
            "retrieval": "R@5 / R@10 / R@20",
            "ranking": "MRR / nDCG@10 / P@5 (+ optional post_rank_R@k)",
            "vector": "OFF",
        },
        "Temporal": {
            "n_pass": temporal.get("n_pass"),
            "n": temporal.get("n"),
            "pass_rate": temporal.get("pass_rate"),
            "note": "T19 evaluator CAVEAT aligned; product unchanged",
        },
        "Retrieval_Recall": {
            "baseline": {
                "R@5": recall_frozen.get("macro_recall@5"),
                "R@10": recall_frozen.get("macro_recall@10"),
                "R@20": recall_frozen.get("macro_recall@20"),
            },
            "current": {
                "R@5": recall.get("macro_recall@5"),
                "R@10": recall.get("macro_recall@10"),
                "R@20": recall.get("macro_recall@20"),
            },
        },
        "Ranking": {
            "production": "KEEP current production ranking",
            "baseline": rb,
            "experiment_v1_1": rv,
            "delta_v1_1_minus_baseline": delta,
            "focus_R06_R13_R18": {"regressions": regressions, "improvements": improvements},
            "decision": decision,
        },
        "Evidence_v2": {
            "pass_rate": evidence.get("pass_rate"),
            "coverage": evidence.get("macro_evidence_coverage"),
            "correctness": evidence.get("macro_evidence_correctness"),
            "unsupported_rate": evidence.get("macro_unsupported_claim_rate"),
            "citation": evidence.get("macro_citation_correctness"),
            "gold": evidence.get("gold"),
            "note": "includes adversarial unsupported-claim items; not an external quality claim",
        },
        "Answer_v2": {
            "pass_rate": answer.get("pass_rate"),
            "accuracy": answer.get("macro_accuracy"),
            "completeness": answer.get("macro_completeness"),
            "faithfulness": answer.get("macro_faithfulness"),
            "citation": answer.get("macro_citation_correctness"),
            "abstention": answer.get("macro_abstention_correctness"),
            "nonempty": answer.get("macro_nonempty"),
            "note": "hard-gate scoring; pass_rate not for external brag",
        },
        "Vector": {"status": "OFF"},
    }

    out = REPORTS / "QUALITY_V2_1_SUMMARY.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0 if all(s.get("rc", 1) == 0 for s in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
