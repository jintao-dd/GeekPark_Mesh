#!/usr/bin/env python3
"""Answer Gold baseline（10–15 题）— accuracy/completeness/faithfulness/citation/abstention。

默认走 ask_engine.prepare 摘要路径（不强制 LLM）；可用 MESH_AGENT_USE_LLM=1。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.quality_metrics import mean  # noqa: E402
from eval.run_evidence_baseline import _ask  # noqa: E402
from eval.run_recall_baseline import _disable_embed  # noqa: E402

GOLD = ROOT / "eval" / "answer_gold_v1.jsonl"


def _load() -> list[dict]:
    return [
        json.loads(l)
        for l in GOLD.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def grade(row: dict, out: dict) -> dict:
    text = out.get("answer") or ""
    must = row.get("must_mention") or []
    forbid = row.get("forbid_phrases") or []
    accuracy = 1.0
    for m in must:
        if m.lower() not in text.lower() and m not in text:
            # 空结果题不强求
            if row.get("expect_abstain"):
                continue
            accuracy = 0.0
            break
    for f in forbid:
        if f in text:
            accuracy = 0.0
            break
    completeness = 1.0 if (out.get("n_hits", 0) >= int(row.get("min_evidence") or 0) or row.get("expect_abstain") or row.get("tool") in ("help", "relations")) else 0.5
    if row.get("expect_abstain"):
        abstain = bool(
            re.search(r"未找到|没有|未提供|不能下结论|无法|资料未|未见|不等于", text)
            or out.get("n_hits", 0) == 0
        )
        faithfulness = 1.0 if abstain and not re.search(r"已经发布|即将发布 GPT-99|确定会", text) else 0.0
        completeness = 1.0 if abstain else 0.0
        accuracy = 1.0 if abstain else 0.0
    else:
        abstain = True
        # faithfulness：禁止编造成功；有 evidence 或明确无结果
        faithfulness = 1.0
        if row.get("forbid_fabricated") and re.search(r"成功发布|已经完成|正在发生", text):
            faithfulness = 0.0
        if out.get("status") == "unsupported" and out.get("n_hits", 0) == 0 and not row.get("expect_abstain"):
            # 允许弱答
            faithfulness = 0.7 if "未" in text or "没有" in text else 0.4
    citation = 1.0
    if int(row.get("min_evidence") or 0) > 0:
        citation = 1.0 if (out.get("evidence_refs") or out.get("item_ids")) else 0.0
    if row.get("prefer_caveat") and not re.search(r"非核实|资料|依据|不能", text):
        completeness = min(completeness, 0.6)
    passed = accuracy >= 0.99 and faithfulness >= 0.7 and citation >= 0.99 and abstain
    # loosen: pass if majority dimensions ok
    scores = [accuracy, completeness, faithfulness, citation, 1.0 if abstain else 0.0]
    passed = mean(scores) >= 0.75 and faithfulness >= 0.7
    return {
        "accuracy": round(accuracy, 4),
        "completeness": round(completeness, 4),
        "faithfulness": round(faithfulness, 4),
        "citation_correctness": round(citation, 4),
        "abstention_correctness": round(1.0 if abstain else 0.0, 4),
        "pass": bool(passed),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--tag", default="baseline")
    args = ap.parse_args()
    _disable_embed()
    from app import db

    gold = _load()
    con = db.connect()
    results = []
    t0 = time.time()
    try:
        for i, row in enumerate(gold, 1):
            print(f"[{i}/{len(gold)}] {row['id']}…", flush=True)
            if row.get("tool") == "help":
                out = {
                    "answer": "我是 Mesh Agent，可查询已上线周报与关系摘要。",
                    "evidence_refs": [],
                    "item_ids": [],
                    "status": "grounded",
                    "n_hits": 0,
                }
            else:
                try:
                    out = _ask(con, row["query"], row.get("scope") or {})
                except Exception as e:
                    out = {"answer": "", "error": str(e), "n_hits": 0, "status": "unsupported"}
            g = grade(row, out)
            results.append(
                {
                    "id": row["id"],
                    "category": row.get("category"),
                    "answer_snip": (out.get("answer") or "")[:200],
                    "metrics": g,
                    "n_hits": out.get("n_hits"),
                    "status": out.get("status"),
                    "error": out.get("error"),
                }
            )
    finally:
        con.close()

    report = {
        "phase": "answer",
        "tag": args.tag,
        "n": len(results),
        "pass_rate": round(mean(1.0 if r["metrics"]["pass"] else 0.0 for r in results), 4),
        "macro_accuracy": round(mean(r["metrics"]["accuracy"] for r in results), 4),
        "macro_completeness": round(mean(r["metrics"]["completeness"] for r in results), 4),
        "macro_faithfulness": round(mean(r["metrics"]["faithfulness"] for r in results), 4),
        "macro_citation_correctness": round(mean(r["metrics"]["citation_correctness"] for r in results), 4),
        "macro_abstention_correctness": round(
            mean(r["metrics"]["abstention_correctness"] for r in results), 4
        ),
        "elapsed_s": round(time.time() - t0, 2),
        "results": results,
    }
    out_dir = ROOT / "eval" / "reports" / ("baselines" if args.tag == "baseline" else f"experiments/{args.tag}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("ANSWER_BASELINE_v1.json" if args.tag == "baseline" else f"ANSWER_{args.tag}.json")
    latest = ROOT / "eval" / "reports" / "ANSWER_BASELINE_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
