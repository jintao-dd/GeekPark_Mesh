#!/usr/bin/env python3
"""Agent Quality v2.2 · Ranking v1.2 (R18/R12) + Answer hard must_mention/abstention.

不动 Recall / Vector / Agent Contract。每次输出 vs frozen baseline 的 delta。
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
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


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
    ap.add_argument("--skip-recall", action="store_true", help="use frozen recall numbers")
    args = ap.parse_args()
    t0 = time.time()
    steps = []

    if not args.skip_temporal:
        steps.append({"step": "temporal", "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])})

    if not args.skip_recall:
        steps.append(
            {
                "step": "recall",
                "rc": _py(["eval/run_recall_baseline.py", "--reuse-env-db"], {"MESH_RECALL_USE_EMBED": "0"}),
            }
        )

    steps.append({"step": "ranking_baseline", "rc": _py(["eval/run_ranking_baseline.py", "--reuse-env-db", "--profile", "baseline"])})
    steps.append(
        {
            "step": "ranking_v1_2",
            "rc": _py(
                ["eval/run_ranking_baseline.py", "--reuse-env-db", "--profile", "v1_2", "--tag", "ranking_v1_2"]
            ),
        }
    )
    steps.append({"step": "evidence_v2", "rc": _py(["eval/run_evidence_baseline.py", "--reuse-env-db", "--tag", "evidence_v2"])})
    steps.append({"step": "answer_v2", "rc": _py(["eval/run_answer_v2.py", "--reuse-env-db", "--tag", "answer_v2_2"])})

    temporal = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    recall = _load(REPORTS / "RECALL_BASELINE_latest.json")
    recall_f = _load(REPORTS / "baselines" / "RECALL_BASELINE_v1.json") or recall
    rb = _load(REPORTS / "RANKING_BASELINE_latest.json")
    rv = _load(REPORTS / "experiments" / "ranking_v1_2" / "RANKING_ranking_v1_2_latest.json")
    ev = _load(REPORTS / "EVIDENCE_evidence_v2_latest.json") or _load(
        REPORTS / "experiments" / "evidence_v2" / "EVIDENCE_evidence_v2.json"
    )
    ans = _load(REPORTS / "ANSWER_V2_latest.json")
    ans_prev = _load(REPORTS / "experiments" / "answer_v2" / "ANSWER_answer_v2.json")

    bm = {r["id"]: r for r in (rb.get("results") or [])}
    vm = {r["id"]: r for r in (rv.get("results") or [])}
    focus: dict = {}
    regress = []
    for qid in bm:
        b, v = bm[qid], vm[qid]
        if b.get("failure_pattern") == "ok" and v.get("failure_pattern") != "ok":
            regress.append(qid)
        elif v["metrics"]["ndcg@10"] + 1e-9 < b["metrics"]["ndcg@10"] - 0.005:
            regress.append(qid)
    for qid in ("R06", "R12", "R13", "R18"):
        if qid not in bm or qid not in vm:
            continue
        b, v = bm[qid], vm[qid]
        focus[qid] = {
            "baseline_pat": b.get("failure_pattern"),
            "v1_2_pat": v.get("failure_pattern"),
            "baseline_ranks": b.get("ranks"),
            "v1_2_ranks": v.get("ranks"),
            "ndcg_delta": round(v["metrics"]["ndcg@10"] - b["metrics"]["ndcg@10"], 4),
            "top5_b": (b.get("retrieved_items") or [])[:5],
            "top5_v": (v.get("retrieved_items") or [])[:5],
        }

    rkb, rkv = _rk(rb), _rk(rv)
    delta = {k: round(float(rkv.get(k) or 0) - float(rkb.get(k) or 0), 4) for k in ("mrr", "ndcg@10", "precision@5")}
    r20_ok = float(rkv.get("post_rank_R@20") or 0) + 1e-9 >= float(rkb.get("post_rank_R@20") or 0) - 0.001
    r06_ok = focus.get("R06", {}).get("v1_2_pat") == "ok"
    r18_ok = focus.get("R18", {}).get("ndcg_delta", -1) >= -0.001
    r12_b_top = set(focus.get("R12", {}).get("top5_b") or [])
    r12_v_top = set(focus.get("R12", {}).get("top5_v") or [])
    rel12 = set((bm.get("R12") or {}).get("relevant_items") or [])
    r12_improve = (
        len(rel12 & r12_v_top) >= len(rel12 & r12_b_top)
        and focus.get("R12", {}).get("ndcg_delta", -1) >= -0.01
    )

    merge_ok = (
        r06_ok
        and r18_ok
        and r20_ok
        and not regress
        and (delta.get("mrr") or 0) >= -0.005
        and (delta.get("ndcg@10") or 0) >= -0.005
    )
    decision = (
        "CANDIDATE merge ranking_v1_2 → production (manual confirm)"
        if merge_ok and r12_improve
        else "KEEP current production ranking; ranking_v1_2 experiment-only"
        + (f" (gold regressions: {regress})" if regress else "")
    )

    summary = {
        "phase": "Agent Quality v2.2",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "elapsed_s": round(time.time() - t0, 1),
        "steps": steps,
        "Temporal": {"n_pass": temporal.get("n_pass"), "n": temporal.get("n"), "pass_rate": temporal.get("pass_rate")},
        "Retrieval_Recall_frozen": {
            "R@5": recall_f.get("macro_recall@5"),
            "R@10": recall_f.get("macro_recall@10"),
            "R@20": recall_f.get("macro_recall@20"),
            "current": {
                "R@5": recall.get("macro_recall@5"),
                "R@10": recall.get("macro_recall@10"),
                "R@20": recall.get("macro_recall@20"),
            },
        },
        "Ranking": {
            "baseline": rkb,
            "experiment_v1_2": rkv,
            "delta": delta,
            "focus": focus,
            "regressions": regress,
            "gates": {"r06_ok": r06_ok, "r18_ok": r18_ok, "r20_ok": r20_ok, "r12_improve": r12_improve},
            "decision": decision,
            "production": "KEEP current production ranking until merge_ok",
        },
        "Evidence_v2": {
            "pass_rate": ev.get("pass_rate"),
            "unsupported_rate": ev.get("macro_unsupported_claim_rate"),
            "correctness": ev.get("macro_evidence_correctness"),
        },
        "Answer_v2_2": {
            "pass_rate": ans.get("pass_rate"),
            "accuracy": ans.get("macro_accuracy"),
            "abstention": ans.get("macro_abstention_correctness"),
            "nonempty": ans.get("macro_nonempty"),
            "prev_v2_pass": ans_prev.get("pass_rate"),
            "delta_pass": round(float(ans.get("pass_rate") or 0) - float(ans_prev.get("pass_rate") or 0), 4)
            if ans_prev
            else None,
        },
        "Vector": "OFF",
    }
    out = REPORTS / "QUALITY_V2_2_SUMMARY.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0 if all(s.get("rc", 1) == 0 for s in steps) else 1


if __name__ == "__main__":
    raise SystemExit(main())
