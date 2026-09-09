#!/usr/bin/env python3
"""Answer + Evidence 全 Gold 真实验收（逐题 dump，不追求刷 pass_rate）。

用法：
  PYTHONPATH=. python eval/run_answer_evidence_acceptance.py --reuse-env-db --tag accept_v23d

纪律：不改 Contract；不放宽 Gold；Ranking 冻结；不做 Wiki/ES/RAG。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from eval.quality_metrics import mean  # noqa: E402
from eval.run_answer_v2 import GOLD_V2 as ANSWER_GOLD, _load, grade_v2  # noqa: E402
from eval.run_evidence_baseline import GOLD_V2 as EVIDENCE_GOLD, _ask, grade  # noqa: E402
from eval.run_recall_baseline import _disable_embed  # noqa: E402

# Evidence fail 归因（与 Answer A–H 平行，加前缀 E-）
# A answer错 / B must漏 / C 应abstain未拒 / D 错abstain / E 空 / F evidence不足
# G evidence存在但不支持 / H harness


def _ev_fail_class(row: dict, out: dict, metrics: dict) -> str | None:
    if metrics.get("pass"):
        return None
    if metrics.get("claim_support_leak"):
        return "G"  # 把相关证据误当成支持 / 泄漏 claim
    if row.get("must_abstain_or_caveat") and metrics.get("abstention_ok", 1) < 1:
        return "C"
    expect = (row.get("expect_support") or "").strip()
    obs = (metrics.get("claim_support") or "").strip()
    if expect and obs and expect != obs:
        if expect == "insufficient" and obs == "supported":
            return "G"
        if expect == "supported" and obs == "insufficient":
            return "D"  # 过度拒答 / 支持判定过严
        if expect == "contradicted":
            return "G"
        return "A"
    if row.get("forbid_unsupported") and out.get("status") == "unsupported" and not row.get(
        "allow_empty"
    ):
        if (out.get("n_hits") or 0) == 0:
            return "F"
        return "D"
    must = row.get("must_cite_items") or []
    if must and metrics.get("citation_correctness", 1) < 1:
        return "F"
    if not (out.get("answer") or "").strip():
        return "E"
    return "A"


def _answer_soft_flags(row: dict, out: dict, metrics: dict) -> list[str]:
    """pass 题上的软风险（不计入 fail，供验收盯梢）。"""
    flags: list[str] = []
    text = out.get("answer") or ""
    if metrics.get("pass") and row.get("must_mention"):
        # must 只出现在「相关条目」附录
        body = text.split("相关条目")[0] if "相关条目" in text else text
        for m in row["must_mention"]:
            if m not in body and m in text:
                flags.append("must_mention_only_in_appendix")
                break
    if metrics.get("pass") and not row.get("expect_abstain"):
        if re.search(r"不能下结论|资料未提供可核对", text) and (out.get("n_hits") or 0) > 0:
            flags.append("possible_over_abstain_tone")
    cs = out.get("claim_support") or {}
    if row.get("expect_abstain") and cs.get("support") == "supported":
        flags.append("abstain_query_but_support_supported")
    if (out.get("n_hits") or 0) > 0 and not (out.get("evidence_refs") or []):
        flags.append("hits_without_evidence_refs")
    return flags


def _evidence_soft_flags(row: dict, out: dict, metrics: dict) -> list[str]:
    flags: list[str] = []
    if metrics.get("pass") and row.get("claim_must_not_be_supported"):
        if (out.get("n_hits") or 0) > 0 and metrics.get("claim_support") == "supported":
            flags.append("adversarial_but_marked_supported")
    if metrics.get("pass") and row.get("must_cite_items"):
        cited = set(out.get("item_ids") or [])
        missing = [x for x in row["must_cite_items"] if str(x) not in cited]
        if missing and metrics.get("citation_correctness", 0) >= 1:
            flags.append(f"partial_must_cite_ok_cov_ge_half_missing={missing}")
    if row.get("expect_support") == "supported" and metrics.get("claim_support") == "insufficient":
        flags.append("expect_supported_got_insufficient")
    return flags


def _baseline_meta(con) -> dict:
    meta = {
        "ranking_version": "v1.4",
        "vector": "OFF",
        "agent_contract": "frozen_v1",
        "mesh_agent_use_llm": os.environ.get("MESH_AGENT_USE_LLM", "").strip() or "0",
        "mesh_ranking_quality": os.environ.get("MESH_RANKING_QUALITY", "1"),
        "claim_support_module": "app.agent.claim_support",
        "harness": "eval.run_evidence_baseline._ask",
        "note": "harness 对齐 Temporal early + claim_support；默认不成文 LLM",
    }
    try:
        row = con.execute(
            "SELECT value FROM settings WHERE key IN ('llm_model','LLM_MODEL','mesh_llm_model') LIMIT 1"
        ).fetchone()
        if row:
            meta["model_version"] = str(row[0] or "")[:80]
        else:
            meta["model_version"] = os.environ.get("MESH_LLM_MODEL") or os.environ.get(
                "LLM_MODEL", "unset_or_default"
            )
    except Exception:
        meta["model_version"] = os.environ.get("MESH_LLM_MODEL") or "unset"
    meta["prompt_version"] = "ask_engine+temporal_hard_rules+claim_support"
    return meta


def run_answer(con, gold: list[dict]) -> dict:
    results = []
    for i, row in enumerate(gold, 1):
        print(f"[answer {i}/{len(gold)}] {row['id']}…", flush=True)
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
        soft = _answer_soft_flags(row, out, g)
        results.append(
            {
                "id": row["id"],
                "category": row.get("category"),
                "query": row.get("query"),
                "expect_abstain": bool(row.get("expect_abstain")),
                "must_mention": row.get("must_mention") or [],
                "answer_snip": (out.get("answer") or "")[:280],
                "n_hits": out.get("n_hits"),
                "status": out.get("status"),
                "claim_support": out.get("claim_support"),
                "evidence_refs": (out.get("evidence_refs") or [])[:8],
                "metrics": g,
                "fail_class": g.get("fail_class"),
                "soft_flags": soft,
                "error": out.get("error"),
            }
        )
    fails = [r for r in results if not r["metrics"]["pass"]]
    soft_hits = [r for r in results if r["soft_flags"]]
    return {
        "phase": "answer",
        "n": len(results),
        "pass_rate": round(mean(1.0 if r["metrics"]["pass"] else 0.0 for r in results), 4),
        "n_fail": len(fails),
        "n_soft_flag": len(soft_hits),
        "fails": [
            {
                "id": r["id"],
                "fail_class": r["fail_class"],
                "category": r["category"],
                "query": r["query"],
                "metrics": r["metrics"],
                "answer_snip": r["answer_snip"],
                "claim_support": r["claim_support"],
                "n_hits": r["n_hits"],
            }
            for r in fails
        ],
        "soft_flag_cases": [
            {"id": r["id"], "flags": r["soft_flags"], "answer_snip": r["answer_snip"][:120]}
            for r in soft_hits
        ],
        "results": results,
    }


def run_evidence(con, gold: list[dict]) -> dict:
    results = []
    for i, row in enumerate(gold, 1):
        print(f"[evidence {i}/{len(gold)}] {row['id']}…", flush=True)
        try:
            out = _ask(con, row["query"], row.get("scope") or {})
        except Exception as e:
            out = {
                "answer": "",
                "error": str(e),
                "n_hits": 0,
                "status": "unsupported",
                "evidence_refs": [],
                "item_ids": [],
            }
        g = grade(row, out)
        fc = _ev_fail_class(row, out, g)
        if fc:
            g["fail_class"] = fc
        soft = _evidence_soft_flags(row, out, g)
        results.append(
            {
                "id": row["id"],
                "category": row.get("category"),
                "query": row.get("query"),
                "expect_support": row.get("expect_support"),
                "claim_schema": row.get("claim_schema"),
                "must_cite_items": row.get("must_cite_items") or [],
                "answer_snip": (out.get("answer") or "")[:280],
                "n_hits": out.get("n_hits"),
                "status": out.get("status"),
                "claim_support": out.get("claim_support"),
                "evidence_refs": (out.get("evidence_refs") or [])[:8],
                "item_ids": (out.get("item_ids") or [])[:12],
                "metrics": g,
                "fail_class": fc,
                "soft_flags": soft,
                "error": out.get("error"),
            }
        )
    fails = [r for r in results if not r["metrics"]["pass"]]
    soft_hits = [r for r in results if r["soft_flags"]]
    return {
        "phase": "evidence",
        "n": len(results),
        "pass_rate": round(mean(1.0 if r["metrics"]["pass"] else 0.0 for r in results), 4),
        "n_fail": len(fails),
        "n_soft_flag": len(soft_hits),
        "fails": [
            {
                "id": r["id"],
                "fail_class": r["fail_class"],
                "category": r["category"],
                "query": r["query"],
                "expect_support": r["expect_support"],
                "obs_support": (r.get("claim_support") or {}).get("support"),
                "metrics": r["metrics"],
                "answer_snip": r["answer_snip"],
                "item_ids": r["item_ids"],
            }
            for r in fails
        ],
        "soft_flag_cases": [
            {"id": r["id"], "flags": r["soft_flags"], "support": (r.get("claim_support") or {}).get("support")}
            for r in soft_hits
        ],
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--tag", default="accept_v23d")
    ap.add_argument("--answer-gold", default="")
    ap.add_argument("--evidence-gold", default="")
    args = ap.parse_args()
    _disable_embed()
    from app import db

    answer_path = Path(args.answer_gold) if args.answer_gold else ANSWER_GOLD
    evidence_path = Path(args.evidence_gold) if args.evidence_gold else EVIDENCE_GOLD
    con = db.connect()
    t0 = time.time()
    try:
        baseline = _baseline_meta(con)
        answer = run_answer(con, _load(answer_path))
        evidence = run_evidence(con, _load(evidence_path))
    finally:
        con.close()

    report = {
        "phase": "answer_evidence_acceptance",
        "tag": args.tag,
        "baseline": baseline,
        "elapsed_s": round(time.time() - t0, 2),
        "answer_gold": answer_path.name,
        "evidence_gold": evidence_path.name,
        "answer": {k: v for k, v in answer.items() if k != "results"},
        "evidence": {k: v for k, v in evidence.items() if k != "results"},
        "answer_results": answer["results"],
        "evidence_results": evidence["results"],
        "verdict": {
            "answer_hard_clean": answer["n_fail"] == 0,
            "evidence_hard_clean": evidence["n_fail"] == 0,
            "has_soft_risks": (answer["n_soft_flag"] + evidence["n_soft_flag"]) > 0,
            "ready_for_ontology_closeout": answer["n_fail"] == 0 and evidence["n_fail"] == 0,
            "ready_for_model_ab": False,  # 显式：本轮不做模型 A/B
        },
        "remaining_for_triage": {
            "answer_fails": answer["fails"],
            "evidence_fails": evidence["fails"],
            "answer_soft": answer["soft_flag_cases"],
            "evidence_soft": evidence["soft_flag_cases"],
        },
    }
    out_dir = ROOT / "eval" / "reports" / "experiments" / args.tag
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"ACCEPT_{args.tag}.json"
    latest = ROOT / "eval" / "reports" / "ANSWER_EVIDENCE_ACCEPTANCE_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = {
        "tag": args.tag,
        "baseline": baseline,
        "answer_pass_rate": answer["pass_rate"],
        "answer_fails": [f["id"] for f in answer["fails"]],
        "answer_fail_classes": {f["id"]: f["fail_class"] for f in answer["fails"]},
        "evidence_pass_rate": evidence["pass_rate"],
        "evidence_fails": [f["id"] for f in evidence["fails"]],
        "evidence_fail_classes": {f["id"]: f["fail_class"] for f in evidence["fails"]},
        "soft_answer": answer["soft_flag_cases"],
        "soft_evidence": evidence["soft_flag_cases"],
        "verdict": report["verdict"],
        "wrote": str(path),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if answer["n_fail"] == 0 and evidence["n_fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
