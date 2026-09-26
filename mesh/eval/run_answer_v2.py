#!/usr/bin/env python3
"""Answer Gold v2 — 硬检查：非空答案、Accuracy、Faithfulness、Abstention。

不再允许「有 evidence / 平均分 ≥0.75」假通过。
默认 gold：answer_gold_v2.jsonl（不存在则回退 v1）。
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

GOLD_V2 = ROOT / "eval" / "answer_gold_v2.jsonl"
GOLD_V1 = ROOT / "eval" / "answer_gold_v1.jsonl"


def _load(path: Path) -> list[dict]:
    return [
        json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def _fail_class(row: dict, out: dict, metrics: dict) -> str | None:
    """A–H 归因（不放宽门槛）。"""
    if metrics.get("pass"):
        return None
    text = (out.get("answer") or "").strip()
    if not text:
        return "E"  # 空答案
    if metrics.get("must_mention_ok", 1) < 1:
        # harness 误走拒答而缺 must → 常为 H；否则 B
        if out.get("n_hits", 0) == 0 and row.get("category") == "temporal_hard":
            return "H"
        return "B"
    if row.get("expect_abstain") and metrics.get("abstention_correctness", 1) < 1:
        return "C"
    if (not row.get("expect_abstain")) and re.search(
        r"未找到|不能下结论|资料未提供", text
    ) and (out.get("n_hits") or 0) > 0:
        return "D"
    support = (out.get("claim_support") or {}).get("support")
    if support == "insufficient" and metrics.get("accuracy", 1) < 1:
        return "G"
    if (out.get("n_hits") or 0) == 0 and metrics.get("accuracy", 1) < 1:
        return "F"
    if metrics.get("faithfulness", 1) < 1 or metrics.get("accuracy", 1) < 1:
        return "A"
    return "A"


def grade_v2(row: dict, out: dict) -> dict:
    """硬门槛：must_mention / nonempty / faithfulness / abstention；Evidence 数量不能单独过门。"""
    text = (out.get("answer") or "").strip()
    must = row.get("must_mention") or []
    forbid = row.get("forbid_phrases") or []
    expect_abstain = bool(row.get("expect_abstain"))

    nonempty = bool(text)
    # 空答案一律不过（含 abstain 题）
    if not nonempty:
        return {
            "accuracy": 0.0,
            "completeness": 0.0,
            "faithfulness": 0.0,
            "citation_correctness": 0.0,
            "abstention_correctness": 0.0,
            "nonempty": 0.0,
            "must_mention_ok": 0.0,
            "pass": False,
            "scoring": "v2_2_hard",
            "fail_reason": "empty_answer",
            "fail_class": "E",
        }

    must_ok = True
    for m in must:
        if m.lower() not in text.lower() and m not in text:
            must_ok = False
            break

    accuracy = 1.0 if must_ok else 0.0
    for f in forbid:
        if f and f in text:
            accuracy = 0.0
            break

    abstain_phrase = bool(
        re.search(
            r"未找到|没有|未提供|不能下结论|无法|资料未|未见|不等于|不能确认|无法确认|不能推断",
            text,
        )
    )
    if expect_abstain:
        # 必须有明确拒答措辞；不能靠 evidence 数量 / 空壳结构过门
        abstain_ok = abstain_phrase
        accuracy = 1.0 if abstain_ok and accuracy >= 0.99 else 0.0
        if not abstain_ok:
            accuracy = 0.0
        faithfulness = (
            1.0
            if abstain_ok and not re.search(r"已经发布|即将发布 GPT-99|确定会|成功发布|一定会", text)
            else 0.0
        )
        completeness = 1.0 if abstain_ok else 0.0
    else:
        abstain_ok = True
        completeness = 1.0
        if row.get("prefer_caveat") and not re.search(r"非核实|资料|依据|不能|无法", text):
            completeness = 0.5
        faithfulness = 1.0
        if row.get("forbid_fabricated") and re.search(r"成功发布|已经完成|正在发生", text):
            faithfulness = 0.0
        # 非 abstain：must_mention 是 accuracy 硬条件（已反映在 accuracy）

    # citation：有 min_evidence 时需要 refs；但 citation 单独不能挽救 accuracy/abstain 失败
    citation = 1.0
    if int(row.get("min_evidence") or 0) > 0:
        citation = 1.0 if (out.get("evidence_refs") or out.get("item_ids")) else 0.0

    hard_ok = (
        nonempty
        and accuracy >= 0.99
        and faithfulness >= 0.99
        and must_ok
        and (abstain_ok if expect_abstain else True)
        and citation >= 0.99
    )
    metrics = {
        "accuracy": round(accuracy, 4),
        "completeness": round(completeness, 4),
        "faithfulness": round(faithfulness, 4),
        "citation_correctness": round(citation, 4),
        "abstention_correctness": round(1.0 if abstain_ok else 0.0, 4),
        "nonempty": 1.0,
        "must_mention_ok": 1.0 if must_ok else 0.0,
        "pass": bool(hard_ok),
        "scoring": "v2_2_hard",
    }
    fc = _fail_class(row, out, metrics)
    if fc:
        metrics["fail_class"] = fc
    return metrics


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--tag", default="answer_v2")
    ap.add_argument("--gold", default="")
    args = ap.parse_args()
    _disable_embed()
    from app import db

    gold_path = Path(args.gold) if args.gold else (GOLD_V2 if GOLD_V2.exists() else GOLD_V1)
    gold = _load(gold_path)
    from app import llm as llm_mod

    llm_mod.reset_usage_accum()
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
            g = grade_v2(row, out)
            results.append(
                {
                    "id": row["id"],
                    "category": row.get("category"),
                    "answer_snip": (out.get("answer") or "")[:200],
                    "metrics": g,
                    "n_hits": out.get("n_hits"),
                    "status": out.get("status"),
                    "llm_used": bool(out.get("llm_used")),
                    "error": out.get("error"),
                }
            )
    finally:
        con.close()

    report = {
        "phase": "answer",
        "scoring": "v2_hard",
        "gold": str(gold_path.name),
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
        "macro_nonempty": round(mean(r["metrics"]["nonempty"] for r in results), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "usage": llm_mod.take_usage_accum(),
        "results": results,
        "note": "pass_rate is hard-gate; do not treat as external quality claim alone",
    }
    out_dir = ROOT / "eval" / "reports" / "experiments" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ANSWER_{args.tag}.json"
    latest = ROOT / "eval" / "reports" / "ANSWER_V2_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    fails = [r["id"] for r in results if not r["metrics"]["pass"]]
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    print("fails", fails)
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
