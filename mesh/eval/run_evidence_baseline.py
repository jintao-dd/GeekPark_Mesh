#!/usr/bin/env python3
"""Evidence quality baseline — ClaimBinding / EvidenceRef coverage（不发明 truth score）。

用法：
  PYTHONPATH=. python eval/run_evidence_baseline.py --reuse-env-db
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
from eval.run_recall_baseline import _disable_embed, _parse_issue_scope  # noqa: E402

GOLD = ROOT / "eval" / "evidence_gold_v1.jsonl"
GOLD_V2 = ROOT / "eval" / "evidence_gold_v2.jsonl"


def _load(path: Path | None = None) -> list[dict]:
    p = path or GOLD
    return [
        json.loads(l)
        for l in p.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def _ask(con, query: str, scope: dict) -> dict:
    from app.ask_scope import AskScope
    from app import ask_engine
    from app.agent import temporal as temporal_mod
    from app.agent import claim_support as claim_support_mod
    from app.agent.adapters import _evidence_from_contexts
    from app import tokenize as tok

    slug, df, dt, meta = _parse_issue_scope(con, scope or {}, query=query)
    ask = AskScope(channel="harness", slug=slug, date_from=df, date_to=dt, role="viewer")

    # 对齐生产 ask.published：Temporal early path（不放宽 Gold）
    sem = temporal_mod.resolve_time_semantics(
        query,
        issue_mode=meta.get("issue_mode") or "",
        issue_slug=meta.get("context_slug") or slug or "",
    )
    early = temporal_mod.maybe_direct_answer(query, sem)
    if early:
        return {
            "answer": early,
            "evidence_refs": [],
            "item_ids": [],
            "status": "grounded",
            "n_hits": 0,
            "meta": meta,
            "claim_support": {
                "entity": "",
                "relation": "temporal_rule",
                "temporal": sem.window,
                "evidence_refs": [],
                "provenance": "published_only",
                "support": "supported",
                "reason": "temporal_hard_rule",
            },
        }

    prepared = ask_engine.prepare(con, query, ask)
    contexts = list(prepared.get("contexts") or [])
    # 否认类：对齐明确反证（Evidence label≠Answer safety）
    contexts = claim_support_mod.enrich_contexts_for_denial_counter_evidence(
        con, ask, query, contexts
    )
    evidence = _evidence_from_contexts(contexts, slug)
    terms = [t for t in tok.query_terms(query or "", limit=10) if len(t) >= 2]
    latin = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", query or "")

    def _ctx_blob(c: dict) -> str:
        return " ".join(
            str(c.get(k) or "")
            for k in ("标题", "title", "内容", "body", "snippet", "摘要")
        )

    def _grounded(ctxs: list[dict]) -> bool:
        if not terms and not latin:
            return bool(ctxs)
        for c in ctxs:
            blob = _ctx_blob(c)
            blob_l = blob.lower()
            if any(t in blob for t in terms) or any(w.lower() in blob_l for w in latin):
                return True
        return False

    titles: list[str] = []
    for c in contexts[:8]:
        t = (c.get("标题") or c.get("title") or "").strip()
        if t and t not in titles:
            titles.append(t)

    answer = (prepared.get("direct_answer") or "").strip()
    n_hits = int(prepared.get("n_hits") or prepared.get("n_context") or len(contexts) or 0)
    grounded = _grounded(contexts)

    support_assess = claim_support_mod.assess_claim_support(
        query, contexts=contexts, evidence_refs=evidence
    )
    abstain = claim_support_mod.abstain_answer_for_unsupported_claim(support_assess)
    if abstain:
        return {
            "answer": abstain,
            "evidence_refs": [],
            "item_ids": [],
            "status": "unsupported",
            "n_hits": n_hits,
            "meta": meta,
            "claim_support": support_assess,
            "llm_used": False,
        }

    # 无命中 / 与 query 无词交集 → 明确拒答（空答案与“有 Evidence 就算过”均不允许）
    llm_used = False
    if n_hits == 0 or not contexts or not grounded:
        answer = "未找到与问题直接相关的已上线记录，资料未提供可核对依据，不能下结论。"
        evidence = []
        n_hits = 0
        status = "unsupported"
        support_assess = claim_support_mod.assess_claim_support(
            query, contexts=[], evidence_refs=[]
        )
    else:
        # Fixed-model LLM-on：与 adapters.ask_published 同序（claim_support 之后）
        use_llm = os.environ.get("MESH_AGENT_USE_LLM", "").strip() in ("1", "true", "yes")
        if use_llm and not answer:
            try:
                from app import llm as llm_mod

                tblock = temporal_mod.prompt_block(sem)
                ctxs = [c for c in contexts if isinstance(c, dict)][:12]
                llm_ans = llm_mod.answer_question(query, ctxs, temporal_block=tblock)
                if isinstance(llm_ans, dict):
                    llm_ans = (llm_ans.get("answer") or llm_ans.get("text") or "").strip()
                else:
                    llm_ans = (str(llm_ans) if llm_ans else "").strip()
                if llm_ans:
                    answer = llm_ans
                    llm_used = True
            except Exception:
                llm_used = False
        if not answer:
            bits = []
            for c in contexts[:6]:
                title = (c.get("标题") or c.get("title") or "").strip()
                body = (c.get("内容") or c.get("body") or c.get("snippet") or "").strip()[:80]
                if title and body:
                    bits.append(f"{title}：{body}")
                elif title:
                    bits.append(title)
                elif body:
                    bits.append(body)
            answer = "；".join(bits) if bits else "未找到相关已上线记录，不能下结论。"
        # must_mention 附录仅 deterministic harness；LLM 成文不加，避免污染真实路径
        if (not llm_used) and titles:
            joined = "；".join(titles[:6])
            if joined not in answer:
                answer = f"{answer}\n相关条目：{joined}".strip()
        answer = temporal_mod.apply_hard_rules(answer, sem)
        status = "grounded" if evidence else "weak"
        support_assess = claim_support_mod.assess_claim_support(
            query, contexts=contexts, evidence_refs=evidence, answer=answer
        )

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
        "claim_support": support_assess,
        "llm_used": llm_used,
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
    text = out.get("answer") or ""
    if row.get("must_abstain_or_caveat"):
        abstain_ok = bool(
            re.search(r"未找到|没有|未提供|不能|无法|资料未|未见|不能确认|无法确认|不能下结论", text)
            or out.get("n_hits", 0) == 0
        )
        correct = correct and abstain_ok

    # 对抗：有 evidence ≠ 支持 claim；答案不得断言 forbid_claim_phrases
    claim_leak = False
    if row.get("claim_must_not_be_supported"):
        for p in row.get("forbid_claim_phrases") or []:
            if p and p in text:
                claim_leak = True
                break
        if claim_leak:
            correct = False
        # 对抗题：若有命中证据但仍断言 claim → unsupported_claim=1
        if claim_leak:
            unsupported = True
        elif not abstain_ok and (out.get("n_hits", 0) or 0) > 0:
            # 有证据却无 caveat、也未泄漏短语：仍算未正确处理对抗
            correct = False
            unsupported = True
        elif abstain_ok and not claim_leak:
            # 正确拒答 / caveat：status=unsupported 是预期，不得当 fail
            correct = True
            unsupported = False

    # Claim → Support（v2.3）：expect_support ∈ supported|insufficient|contradicted
    support_obs = ((out.get("claim_support") or {}).get("support") or "").strip()
    expect_support = (row.get("expect_support") or "").strip()
    support_ok = True
    if expect_support:
        support_ok = support_obs == expect_support
        if not support_ok:
            correct = False
    # 对抗题默认期望 insufficient（除非显式 expect_support）
    if row.get("claim_must_not_be_supported") and not expect_support:
        if support_obs == "supported" and not abstain_ok:
            support_ok = False
            correct = False

    return {
        "evidence_coverage": round(cov if must else (1.0 if coverage_ok else 0.0), 4),
        "evidence_correctness": 1.0 if correct else 0.0,
        "unsupported_claim": 1.0 if unsupported else 0.0,
        "citation_correctness": 1.0 if citation_ok else 0.0,
        "abstention_ok": 1.0 if abstain_ok else 0.0,
        "claim_support_leak": 1.0 if claim_leak else 0.0,
        "claim_support": support_obs or "n/a",
        "claim_support_ok": 1.0 if support_ok else 0.0,
        "pass": bool(correct and coverage_ok and abstain_ok and not claim_leak and support_ok),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--tag", default="baseline")
    ap.add_argument("--gold", default="", help="path to gold jsonl; default v1 or v2 by tag")
    args = ap.parse_args()
    _disable_embed()
    from app import db

    if args.gold:
        gold_path = Path(args.gold)
    elif args.tag in ("v2", "evidence_v2") and GOLD_V2.exists():
        gold_path = GOLD_V2
    else:
        gold_path = GOLD
    gold = _load(gold_path)
    from app import llm as llm_mod

    llm_mod.reset_usage_accum()
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
        "gold": gold_path.name,
        "elapsed_s": round(time.time() - t0, 2),
        "usage": llm_mod.take_usage_accum(),
        "results": results,
    }
    out_dir = ROOT / "eval" / "reports" / ("baselines" if args.tag == "baseline" else f"experiments/{args.tag}")
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.tag == "baseline":
        path = out_dir / "EVIDENCE_BASELINE_v1.json"
        latest = ROOT / "eval" / "reports" / "EVIDENCE_BASELINE_latest.json"
    else:
        path = out_dir / f"EVIDENCE_{args.tag}.json"
        latest = ROOT / "eval" / "reports" / f"EVIDENCE_{args.tag}_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
