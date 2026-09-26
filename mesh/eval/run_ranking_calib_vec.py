#!/usr/bin/env python3
"""向量全开 Ranking 校准（可复跑）。

在 tmesh / 本地复现：
  PYTHONPATH=. MESH_RECALL_USE_EMBED=1 MESH_EMBED_ENABLED=1 MESH_VECTOR_ENABLED=1 \\
    python eval/run_ranking_calib_vec.py --reuse-env-db

默认跑三臂并写 Gate：
  A = legacy + quality ON
  B = RRF + quality OFF   ← 生产默认
  C = RRF + quality ON

输出：
  eval/reports/experiments/calib_vec_{A,B,C}/
  eval/reports/experiments/CALIB_VEC_GATE.json
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
REPORTS = ROOT / "eval" / "reports" / "experiments"
KEYS = [
    "macro_mrr",
    "macro_ndcg@10",
    "macro_precision@5",
    "macro_recall@5",
    "macro_recall@10",
    "macro_recall@20",
]

ARMS = (
    ("calib_vec_A", "legacy", "1"),
    ("calib_vec_B", "rrf", "0"),
    ("calib_vec_C", "rrf", "1"),
)


def _run_arm(tag: str, fusion: str, quality: str) -> Path:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["MESH_EMBED_ENABLED"] = "1"
    env["MESH_VECTOR_ENABLED"] = "1"
    env["MESH_RECALL_USE_EMBED"] = "1"
    env["MESH_FUSION"] = fusion
    env["MESH_RANKING_QUALITY"] = quality
    cmd = [
        sys.executable,
        str(ROOT / "eval" / "run_ranking_baseline.py"),
        "--reuse-env-db",
        "--tag",
        tag,
    ]
    print(f"===== {tag} fusion={fusion} quality={quality} embed=ON =====", flush=True)
    t0 = time.time()
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)
    print(f"  elapsed={time.time() - t0:.1f}s", flush=True)
    return REPORTS / tag / f"RANKING_{tag}_latest.json"


def _delta(a: dict, b: dict, k: str) -> float:
    return round(float(b.get(k) or 0) - float(a.get(k) or 0), 4)


def _compare(paths: dict[str, Path]) -> dict:
    data = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in paths.items()}
    print(f"\n{'arm':24s} " + " ".join(f"{k.replace('macro_', '')[:11]:>12s}" for k in KEYS))
    labels = {
        "A": "A legacy + quality ON ",
        "B": "B RRF    + quality OFF",
        "C": "C RRF    + quality ON ",
    }
    for key in ("A", "B", "C"):
        d = data[key]
        print(f"{labels[key]:24s} " + " ".join(f"{str(d.get(k)):>12s}" for k in KEYS))

    for title, src, dst in (
        ("A→B（整包，向量全开）", "A", "B"),
        ("B→C（RRF 固定，开补丁）", "B", "C"),
        ("A→C（RRF + 仍开补丁）", "A", "C"),
    ):
        print(f"\n=== {title} ===")
        for k in KEYS:
            print(f"  {k:20s} {_delta(data[src], data[dst], k):+.4f}")

    print("\n=== 逐题 MRR（有变化）===")
    ra = {r["id"]: r for r in data["A"]["results"]}
    rb = {r["id"]: r for r in data["B"]["results"]}
    rc = {r["id"]: r for r in data["C"]["results"]}
    for qid in sorted(ra):
        ma, mb, mc = (
            ra[qid]["metrics"]["mrr"],
            rb[qid]["metrics"]["mrr"],
            rc[qid]["metrics"]["mrr"],
        )
        if ma != mb or mb != mc:
            print(
                f"  {qid:4s} A={ma:.2f} B={mb:.2f} C={mc:.2f}  "
                f"A→B={mb - ma:+.2f} B→C={mc - mb:+.2f}"
            )

    gate = {
        "p5_ok": data["B"]["macro_precision@5"] >= data["A"]["macro_precision@5"] - 1e-9,
        "r5_ok": data["B"]["macro_recall@5"] >= data["A"]["macro_recall@5"] - 1e-9,
        "mrr_delta": _delta(data["A"], data["B"], "macro_mrr"),
        "ndcg_delta": _delta(data["A"], data["B"], "macro_ndcg@10"),
    }
    # 不低于关向量口径的整包跌幅（-0.0389）
    gate["mrr_not_worse_than_lexical"] = gate["mrr_delta"] >= -0.039
    gate["pass"] = bool(
        gate["p5_ok"] and gate["r5_ok"] and gate["mrr_not_worse_than_lexical"]
    )
    out = {
        "phase": "ranking_calib_vec",
        "arms": {k: {kk: data[k].get(kk) for kk in KEYS} for k in ("A", "B", "C")},
        "gate": gate,
        "artifacts": {k: str(paths[k].relative_to(ROOT)) for k in paths},
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    gate_path = REPORTS / "CALIB_VEC_GATE.json"
    gate_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n=== GATE ===")
    print(json.dumps(gate, ensure_ascii=False, indent=2))
    print("wrote", gate_path)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument(
        "--compare-only",
        action="store_true",
        help="只读已有 latest JSON 做对比，不重跑",
    )
    args = ap.parse_args()
    if args.compare_only:
        paths = {
            "A": REPORTS / "calib_vec_A" / "RANKING_calib_vec_A_latest.json",
            "B": REPORTS / "calib_vec_B" / "RANKING_calib_vec_B_latest.json",
            "C": REPORTS / "calib_vec_C" / "RANKING_calib_vec_C_latest.json",
        }
        for p in paths.values():
            if not p.exists():
                raise SystemExit(f"missing {p}")
    else:
        paths = {}
        for tag, fusion, quality in ARMS:
            p = _run_arm(tag, fusion, quality)
            paths[tag[-1]] = p  # A/B/C
    out = _compare(paths)
    return 0 if out["gate"]["pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
