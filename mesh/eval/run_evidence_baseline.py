#!/usr/bin/env python3
"""Evidence quality baseline — ClaimBinding / EvidenceRef coverage（不发明 truth score）。

用法：
  PYTHONPATH=. python eval/run_evidence_baseline.py --reuse-env-db
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
from eval.run_recall_baseline import _disable_embed, _parse_issue_scope  # noqa: E402

GOLD = ROOT / "eval" / "evidence_gold_v1.jsonl"


def _load() -> list[dict]:
    return [
        json.loads(l)
        for l in GOLD.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def _ask(con, query: str, scope: dict) -> dict:
    from app.ask_scope import AskScope
    from app import ask_engine
    from app.agent import temporal as temporal_mod
    from app.agent.adapters import _evidence_from_contexts

    slug, df, dt, meta = _parse_issue_scope(con, scope or {}, query=query)
    ask = AskScope(channel="harness", slug=slug, date_from=df, date_to=dt, role="viewer")
    prepared = ask_engine.prepare(con, query, ask)
    contexts = list(prepared.get("contexts") or [])
    evidence = _evidence_from_contexts(contexts, slug)
    answer = (prepared.get("direct_answer") or "").strip()
    if not answer and contexts:
        # 无 LLM：用检索摘要，仍可测 citation 覆盖
        bits = []
        for c in contexts[:5]:
            t = (c.get("标题") or c.get("title") or c.get("内容") or "")[:60]
            if t:
                bits.append(t)
        answer = "；".join(bits) if bits else "未找到相关已上线记录。"
    sem = temporal_mod.resolve_time_semantics(
        query, issue_mode=meta.get("issue_mode") or "", issue_slug=meta.get("context_slug") or ""
    )
    answer = temporal_mod.apply_hard_rules(answer, sem)
    n_hits = int(prepared.get("n_hits") or prepared.get("n_context") or 0)
    status = "grounded" if n_hits > 0 and evidence else ("weak" if contexts else "unsupported")
    item_ids = []
    for e in evidence:
        m = re.match(r"ev:item:(\d+)", e)
        if m:
            item_ids.append(m.group(1))
    return {
        "answer": answer,
        "evidence_refs": evidence,
        "item_ids": item_ids,
        "status": status,
        "n_hits": n_hits,
        "meta": meta,
    }


def grade(row: dict, out: dict) -> dict:
    must = [str(x) for x in (row.get("must_cite_items") or [])]
    cited = set(out.get("item_ids") or [])
    cov = 1.0
    if must:
        cov = len(set(must) & cited) / len(must)
    min_ev = int(row.get("expect_min_evidence") or 0)
    coverage_ok = len(out.get("evidence_refs") or []) >= min_ev
    unsupported = out.get("status") == "unsupported" and not row.get("allow_empty")
    if row.get("allow_empty") and out.get("n_hits", 0) == 0:
        unsupported = False
        coverage_ok = True
    citation_ok = cov >= 0.5 if must else True
    # correctness proxy：有 must_cite 时至少命中一条；无 must 时 grounded/weak 即可
    correct = citation_ok and (out.get("status") in ("grounded", "weak") or row.get("allow_empty"))
    if row.get("forbid_unsupported") and unsupported:
        correct = False
    abstain_ok = True
    if row.get("must_abstain_or_caveat"):
        text = out.get("answer") or ""
        abstain_ok = bool(
            re.search(r"未找到|没有|未提供|不能|无法|资料未|未见", text)
            or out.get("n_hits", 0) == 0
        )
        correct = correct and abstain_ok
    return {
        "evidence_coverage": round(cov if must else (1.0 if coverage_ok else 0.0), 4),
        "evidence_correctness": 1.0 if correct else 0.0,
        "unsupported_claim": 1.0 if unsupported else 0.0,
        "citation_correctness": 1.0 if citation_ok else 0.0,
        "abstention_ok": 1.0 if abstain_ok else 0.0,
        "pass": bool(correct and coverage_ok and abstain_ok),
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
            try:
                out = _ask(con, row["query"], row.get("scope") or {})
                g = grade(row, out)
            except Exception as e:
                out = {"error": str(e)}
                g = {
                    "evidence_coverage": 0,
                    "evidence_correctness": 0,
                    "unsupported_claim": 1,
                    "citation_correctness": 0,
                    "abstention_ok": 0,
                    "pass": False,
                }
            results.append({"id": row["id"], "category": row.get("category"), "out": {
                "status": out.get("status"),
                "n_hits": out.get("n_hits"),
                "evidence_refs": (out.get("evidence_refs") or [])[:8],
                "item_ids": out.get("item_ids") or [],
                "answer_snip": (out.get("answer") or "")[:160],
                "error": out.get("error"),
            }, "metrics": g})
    finally:
        con.close()

    report = {
        "phase": "evidence",
        "tag": args.tag,
        "n": len(results),
        "pass_rate": round(mean(1.0 if r["metrics"]["pass"] else 0.0 for r in results), 4),
        "macro_evidence_coverage": round(mean(r["metrics"]["evidence_coverage"] for r in results), 4),
        "macro_evidence_correctness": round(mean(r["metrics"]["evidence_correctness"] for r in results), 4),
        "macro_unsupported_claim_rate": round(mean(r["metrics"]["unsupported_claim"] for r in results), 4),
        "macro_citation_correctness": round(mean(r["metrics"]["citation_correctness"] for r in results), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "results": results,
    }
    out_dir = ROOT / "eval" / "reports" / ("baselines" if args.tag == "baseline" else f"experiments/{args.tag}")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / ("EVIDENCE_BASELINE_v1.json" if args.tag == "baseline" else f"EVIDENCE_{args.tag}.json")
    latest = ROOT / "eval" / "reports" / "EVIDENCE_BASELINE_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
