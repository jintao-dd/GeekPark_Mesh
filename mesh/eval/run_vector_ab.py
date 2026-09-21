#!/usr/bin/env python3
"""Retrieval Vector A/B — FTS-only vs FTS+Vector（对照实验，不切主线）。

输出 experiments/vector_ab/ 下两份报告 + DELTA。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _run(embed: bool, tag: str, gold: str = "") -> Path:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT)
    env["MESH_RECALL_USE_EMBED"] = "1" if embed else "0"
    cmd = [
        sys.executable,
        str(ROOT / "eval" / "run_recall_baseline.py"),
        "--reuse-env-db",
        "--tag",
        f"vector_ab_{tag}",
    ]
    if gold:
        cmd += ["--gold", gold]
    print("RUN", tag, "embed=", embed, flush=True)
    subprocess.check_call(cmd, cwd=str(ROOT), env=env)
    src = ROOT / "eval" / "reports" / f"RECALL_vector_ab_{tag}_latest.json"
    out_dir = ROOT / "eval" / "reports" / "experiments" / "vector_ab"
    out_dir.mkdir(parents=True, exist_ok=True)
    dst = out_dir / f"RECALL_{tag}.json"
    data = json.loads(src.read_text(encoding="utf-8"))
    data["experiment"] = tag
    data["embed"] = embed
    dst.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="", help="gold jsonl 路径")
    ap.add_argument("--tag", default="", help="输出标签前缀")
    args = ap.parse_args()
    pre = f"{args.tag}_" if args.tag else ""
    fts = _run(False, f"{pre}fts_only", gold=args.gold)
    # Vector may be slow / unavailable — still attempt
    try:
        vec = _run(True, f"{pre}fts_plus_vector", gold=args.gold)
    except Exception as e:
        out_dir = ROOT / "eval" / "reports" / "experiments" / "vector_ab"
        delta = {
            "status": "vector_run_failed",
            "error": str(e),
            "fts_only": str(fts),
            "recommendation": "keep FTS-only mainline",
        }
        (out_dir / f"{pre}VECTOR_AB_DELTA.json").write_text(
            json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(json.dumps(delta, ensure_ascii=False, indent=2))
        return 0

    a = json.loads(fts.read_text(encoding="utf-8"))
    b = json.loads(vec.read_text(encoding="utf-8"))
    keys = [
        "macro_recall@5",
        "macro_recall@10",
        "macro_recall@20",
        "macro_chunk_recall@5",
        "macro_chunk_recall@10",
        "macro_chunk_in_pool",
    ]
    delta = {
        "status": "ok",
        "gold": a.get("gold"),
        "fts_only": {k: a.get(k) for k in keys},
        "fts_plus_vector": {k: b.get(k) for k in keys},
        "delta": {k: round(float(b.get(k) or 0) - float(a.get(k) or 0), 4) for k in keys},
        "recommendation": "keep FTS-only mainline"
        if all(float(b.get(k) or 0) <= float(a.get(k) or 0) + 0.01 for k in keys)
        else "consider optional vector lane (still not default)",
    }
    # per-qid: who newly recalled
    ar = {r["id"]: set(r.get("retrieved_items") or r.get("hit_items") or []) for r in a.get("results") or []}
    # use relevant intersection
    new_hits = []
    noise = []
    for r in b.get("results") or []:
        qid = r["id"]
        rel = set(str(x) for x in (r.get("relevant_items") or []))
        bret = set(r.get("retrieved_items") or [])
        aret = ar.get(qid) or set()
        # approximate: items in b top20 relevant not in a — need full retrieved; use hit_items if present
        bhit = set(r.get("hit_items") or []) & rel
        # from a results
        arow = next((x for x in (a.get("results") or []) if x["id"] == qid), {})
        ahit = set(arow.get("hit_items") or []) & rel
        gained = sorted(bhit - ahit)
        if gained:
            new_hits.append({"id": qid, "gained_relevant": gained})
        # noise proxy: more retrieved non-relevant in top5
        btop = list(r.get("retrieved_items") or [])[:5]
        atop = list(arow.get("retrieved_items") or [])[:5]
        bn = len([x for x in btop if x not in rel])
        an = len([x for x in atop if x not in rel])
        if bn > an:
            noise.append({"id": qid, "top5_noise_delta": bn - an})
    delta["new_relevant"] = new_hits
    delta["noise_increase"] = noise
    out = ROOT / "eval" / "reports" / "experiments" / "vector_ab" / f"{pre}VECTOR_AB_DELTA.json"
    out.write_text(json.dumps(delta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(delta, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
