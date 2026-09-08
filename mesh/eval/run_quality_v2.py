#!/usr/bin/env python3
"""Agent Quality v2 · Unified Runner

一次输出 Temporal / Recall / Ranking / Evidence / Answer（+ 可选 Vector A/B）。
禁止覆盖 frozen baselines；新结果写 latest + experiments。

用法：
  PYTHONPATH=. python eval/run_quality_v2.py --reuse-env-db
  PYTHONPATH=. python eval/run_quality_v2.py --reuse-env-db --skip-vector
  PYTHONPATH=. python eval/run_quality_v2.py --reuse-env-db --ranking-profile v1
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-vector", action="store_true")
    ap.add_argument("--skip-temporal", action="store_true")
    ap.add_argument("--ranking-profile", choices=("baseline", "v1", "both"), default="both")
    args = ap.parse_args()

    t0 = time.time()
    steps = []

    if not args.skip_temporal:
        rc = _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])
        steps.append({"step": "temporal", "rc": rc})

    # Recall FTS baseline (preserve: copy to baselines if missing)
    os.environ["MESH_RECALL_USE_EMBED"] = "0"
    rc = _py(["eval/run_recall_baseline.py", "--reuse-env-db"], {"MESH_RECALL_USE_EMBED": "0"})
    steps.append({"step": "recall_fts", "rc": rc})
    recall = _load(REPORTS / "RECALL_BASELINE_latest.json")
    frozen = REPORTS / "baselines" / "RECALL_BASELINE_v1.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    if not frozen.exists() and recall:
        frozen.write_text(json.dumps(recall, ensure_ascii=False, indent=2), encoding="utf-8")

    if args.ranking_profile in ("baseline", "both"):
        rc = _py(["eval/run_ranking_baseline.py", "--reuse-env-db", "--profile", "baseline"])
        steps.append({"step": "ranking_baseline", "rc": rc})
    if args.ranking_profile in ("v1", "both"):
        rc = _py(
            ["eval/run_ranking_baseline.py", "--reuse-env-db", "--profile", "v1", "--tag", "ranking_v1"]
        )
        steps.append({"step": "ranking_v1", "rc": rc})

    rc = _py(["eval/run_evidence_baseline.py", "--reuse-env-db", "--tag", "baseline"])
    steps.append({"step": "evidence", "rc": rc})
    rc = _py(["eval/run_answer_baseline.py", "--reuse-env-db", "--tag", "baseline"])
    steps.append({"step": "answer", "rc": rc})

    if not args.skip_vector:
        rc = _py(["eval/run_vector_ab.py"])
        steps.append({"step": "vector_ab", "rc": rc})

    temporal = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    ranking_b = _load(REPORTS / "RANKING_BASELINE_latest.json")
    ranking_v1 = _load(REPORTS / "experiments" / "ranking_v1" / "RANKING_ranking_v1_latest.json")
    if not ranking_v1:
        # find newest
        d = REPORTS / "experiments" / "ranking_v1"
        if d.exists():
            cands = sorted(d.glob("RANKING_*.json"))
            if cands:
                ranking_v1 = _load(cands[-1])
    evidence = _load(REPORTS / "EVIDENCE_BASELINE_latest.json")
    answer = _load(REPORTS / "ANSWER_BASELINE_latest.json")
    vector = _load(REPORTS / "experiments" / "vector_ab" / "VECTOR_AB_DELTA.json")
    recall_frozen = _load(frozen) if frozen.exists() else recall

    def _rk(d: dict) -> dict:
        """Ranking 主指标；post_rank_R@k 仅作排序后 Top-K，勿与 Retrieval Recall 混用。"""
        return {
            "mrr": d.get("macro_mrr"),
            "ndcg@10": d.get("macro_ndcg@10"),
            "precision@5": d.get("macro_precision@5"),
            "post_rank_R@5": d.get("macro_recall@5"),
            "post_rank_R@10": d.get("macro_recall@10"),
        }

    summary = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "steps": steps,
        "Temporal": {
            "baseline": "24/24 PASS" if (temporal.get("pass") or temporal.get("n_pass") == temporal.get("n")) else temporal.get("summary") or temporal,
            "current": temporal.get("n_pass") or temporal.get("passed") or temporal.get("summary"),
            "delta": "0 (frozen PASS)",
            "n": temporal.get("n") or temporal.get("total"),
        },
        "Recall": {
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
            "delta": {
                k: round(
                    float(recall.get(f"macro_recall@{k[2:]}") or 0)
                    - float(recall_frozen.get(f"macro_recall@{k[2:]}") or 0),
                    4,
                )
                for k in ("R@5", "R@10", "R@20")
            },
        },
        "Ranking": {
            "baseline": _rk(ranking_b),
            "experiment_v1": _rk(ranking_v1) if ranking_v1 else None,
            "delta_v1_minus_baseline": {
                k: round(
                    float((_rk(ranking_v1) or {}).get(k) or 0) - float((_rk(ranking_b) or {}).get(k) or 0),
                    4,
                )
                for k in ("mrr", "ndcg@10", "precision@5")
            }
            if ranking_v1 and ranking_b
            else None,
            "decision": None,
            "production": "KEEP current production ranking; ranking_v1 remains experiment-only",
        },
        "Evidence": {
            "baseline": {
                "pass_rate": evidence.get("pass_rate"),
                "coverage": evidence.get("macro_evidence_coverage"),
                "correctness": evidence.get("macro_evidence_correctness"),
                "unsupported_rate": evidence.get("macro_unsupported_claim_rate"),
                "citation": evidence.get("macro_citation_correctness"),
            },
            "current": "same as baseline (first freeze)",
            "delta": "n/a first baseline",
        },
        "Answer": {
            "baseline": {
                "pass_rate": answer.get("pass_rate"),
                "accuracy": answer.get("macro_accuracy"),
                "completeness": answer.get("macro_completeness"),
                "faithfulness": answer.get("macro_faithfulness"),
                "citation": answer.get("macro_citation_correctness"),
                "abstention": answer.get("macro_abstention_correctness"),
            },
            "current": "same as baseline (first freeze)",
            "delta": "n/a first baseline",
        },
        "Vector_AB": vector or {"status": "skipped"},
    }

    # Ranking decision：宏指标正向仍须人工确认误杀；默认不自动合并生产
    dlt = summary["Ranking"].get("delta_v1_minus_baseline") or {}
    if dlt:
        improved = (dlt.get("mrr") or 0) >= 0 and (dlt.get("ndcg@10") or 0) >= 0 and (
            dlt.get("precision@5") or 0
        ) >= -0.01
        if improved and ((dlt.get("ndcg@10") or 0) > 0.005 or (dlt.get("mrr") or 0) > 0.005):
                summary["Ranking"]["decision"] = (
                "KEEP current production ranking; ranking_v1 remains experiment-only "
                "until per-qid regressions (esp. ok→top5_miss) are zero; no auto-merge"
            )
        else:
            summary["Ranking"]["decision"] = (
                "KEEP current production ranking; ranking_v1 remains experiment-only (no merge)"
            )

    out = REPORTS / "QUALITY_V2_OVERNIGHT_SUMMARY.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0 if all(s.get("rc", 1) == 0 for s in steps if s["step"] != "vector_ab") else 1


if __name__ == "__main__":
    raise SystemExit(main())
