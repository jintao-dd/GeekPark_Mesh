#!/usr/bin/env python3
"""Ranking Phase 0/1 · MRR / nDCG@10 / Precision@5.

用法：
  PYTHONPATH=. python eval/run_ranking_baseline.py --reuse-env-db
  PYTHONPATH=. python eval/run_ranking_baseline.py --reuse-env-db --profile v1
  PYTHONPATH=. python eval/run_ranking_baseline.py --reuse-env-db --tag baseline

不覆盖旧 baseline：写入 reports/baselines 或 reports/experiments/<tag>。
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

from eval.quality_metrics import mean, mrr, ndcg_at, precision_at, recall_at  # noqa: E402
from eval.run_recall_baseline import (  # noqa: E402
    _disable_embed,
    _parse_issue_scope,
)

from app.ranking_quality import (  # noqa: E402
    apply_ranking_v1_1,
    apply_ranking_v1_2,
    apply_ranking_v1_3,
    apply_ranking_v1_4,
)

GOLD_PATH = ROOT / "eval" / "ranking_gold_v1.jsonl"


def _load_gold() -> list[dict]:
    rows = []
    for line in GOLD_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def _retrieve_hits(con, query: str, scope: dict, *, top_n: int = 40) -> tuple[list[dict], dict]:
    from app.ask_scope import AskScope
    from app import retriever, embeddings

    slug, date_from, date_to, scope_meta = _parse_issue_scope(con, scope, query=query)
    ask_scope = AskScope(
        channel="harness",
        slug=slug,
        team="",
        date_from=date_from,
        date_to=date_to,
        role="viewer",
    )
    qvec = None
    if embeddings.is_configured():
        qvec = embeddings.embed_one(query)
    mode, hits, meta = retriever.retrieve(
        con, query, ask_scope, intent=None, query_vec=qvec
    )
    return list(hits or [])[:top_n], {
        "mode": mode,
        "slug": slug,
        "date_from": date_from,
        "date_to": date_to,
        "n_hits_raw": len(hits or []),
        "used_vector": bool((meta or {}).get("used_vector")),
        **scope_meta,
    }


def _item_ids(hits: list[dict]) -> list[str]:
    out, seen = [], set()
    for h in hits:
        iid = h.get("item_id")
        if iid is None or str(iid).strip() == "":
            continue
        s = str(int(iid)) if str(iid).isdigit() else str(iid)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _query_terms(q: str) -> list[str]:
    from app import tokenize as tok

    return tok.query_terms(q, limit=8)


def apply_ranking_v1(hits: list[dict], query: str) -> list[dict]:
    """最小排序实验：精确实体/专名命中、来源、短词标题命中。不改召回集合。"""
    terms = _query_terms(query)
    latin = re.findall(r"[A-Za-z][A-Za-z0-9_.-]{1,}", query or "")
    out = []
    for h in hits:
        h = dict(h)
        bonus = 0.0
        title = (h.get("title") or "")
        body = (h.get("body") or "")
        title_l, body_l = title.lower(), body.lower()
        for t in terms:
            if len(t) >= 2 and t in title:
                bonus += 0.45 if len(t) >= 4 else 0.28
            elif len(t) >= 2 and t in body:
                bonus += 0.06
        for w in latin:
            wl = w.lower()
            if wl in title_l:
                bonus += 0.4
            elif wl in body_l:
                bonus += 0.1
        src = h.get("source") or ""
        if src == "item_entity_facts":
            bonus += 0.15
        elif src == "item_facts":
            bonus += 0.08
        # 标题几乎整词命中 query 中最长中文片
        long = max((t for t in terms if re.search(r"[\u4e00-\u9fff]", t)), key=len, default="")
        if long and len(long) >= 4 and long[:4] in title:
            bonus += 0.2
        h["score"] = float(h.get("score") or 0) - bonus
        h["_rank_v1_bonus"] = bonus
        out.append(h)
    out.sort(key=lambda x: float(x.get("score") or 0))
    return out


def _classify(row: dict, retrieved: list[str], relevant: set[str]) -> str:
    if not relevant:
        return "ok"
    if not retrieved:
        return "empty"
    top5 = set(retrieved[:5])
    if relevant <= top5:
        return "ok"
    if relevant & set(retrieved[:20]):
        miss5 = sorted(relevant - top5)
        return f"top5_miss:{','.join(miss5[:4])}"
    return "not_in_top20"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument(
        "--profile",
        choices=("baseline", "v1", "v1_1", "v1_2", "v1_3", "v1_4"),
        default="baseline",
    )
    ap.add_argument("--tag", default="")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="")
    args = ap.parse_args()

    _disable_embed()
    from app import db

    gold = _load_gold()
    if args.ids.strip():
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        gold = [r for r in gold if r.get("id") in want]
    if args.limit > 0:
        gold = gold[: args.limit]

    tag = args.tag or (f"ranking_{args.profile}")
    if args.profile == "baseline" and not args.tag:
        out_dir = ROOT / "eval" / "reports" / "baselines"
    else:
        out_dir = ROOT / "eval" / "reports" / "experiments" / tag
    out_dir.mkdir(parents=True, exist_ok=True)

    con = db.connect()
    results = []
    t0 = time.time()
    try:
        for i, row in enumerate(gold, 1):
            qid = row["id"]
            query = row["query"]
            relevant = set(str(x) for x in (row.get("relevant_items") or []))
            grades = {str(k): float(v) for k, v in (row.get("grades") or {}).items()}
            print(f"[{i}/{len(gold)}] {qid} {query[:36]}…", flush=True)
            # production baseline 已合入 ranking_v1_4；实验 profile 先关 quality 再套实验函数，避免双加。
            import os
            prev_rq = os.environ.get("MESH_RANKING_QUALITY")
            if args.profile in ("v1", "v1_1", "v1_2", "v1_3", "v1_4"):
                os.environ["MESH_RANKING_QUALITY"] = "0"
            try:
                hits, info = _retrieve_hits(con, query, row.get("scope") or {})
            finally:
                if prev_rq is None:
                    os.environ.pop("MESH_RANKING_QUALITY", None)
                else:
                    os.environ["MESH_RANKING_QUALITY"] = prev_rq
            if args.profile == "v1":
                hits = apply_ranking_v1(hits, query)
            elif args.profile == "v1_1":
                hits = apply_ranking_v1_1(hits, query, scope_meta=info)
            elif args.profile == "v1_2":
                hits = apply_ranking_v1_2(hits, query, scope_meta=info)
            elif args.profile == "v1_3":
                hits = apply_ranking_v1_3(hits, query, scope_meta=info)
            elif args.profile == "v1_4":
                hits = apply_ranking_v1_4(hits, query, scope_meta=info)
            # profile=baseline → 使用生产 rerank（含 v1.4）
            retrieved = _item_ids(hits)
            metrics = {
                "mrr": round(mrr(relevant, retrieved), 4),
                "ndcg@10": round(ndcg_at(relevant, retrieved, 10, grades=grades), 4),
                "precision@5": round(precision_at(relevant, retrieved, 5), 4),
                "recall@5": round(recall_at(relevant, retrieved, 5), 4),
                "recall@10": round(recall_at(relevant, retrieved, 10), 4),
                "recall@20": round(recall_at(relevant, retrieved, 20), 4),
            }
            ranks = {x: retrieved.index(x) + 1 for x in relevant if x in retrieved}
            results.append(
                {
                    "id": qid,
                    "query": query,
                    "relevant_items": sorted(relevant),
                    "retrieved_items": retrieved[:20],
                    "ranks": ranks,
                    "metrics": metrics,
                    "failure_pattern": _classify(row, retrieved, relevant),
                    "retrieval": info,
                    "profile": args.profile,
                }
            )
            print(
                f"  → MRR={metrics['mrr']:.2f} nDCG@10={metrics['ndcg@10']:.2f} "
                f"P@5={metrics['precision@5']:.2f} pat={results[-1]['failure_pattern']}",
                flush=True,
            )
    finally:
        con.close()

    report = {
        "phase": "ranking",
        "profile": args.profile,
        "tag": tag,
        "n": len(results),
        "macro_mrr": round(mean(r["metrics"]["mrr"] for r in results), 4),
        "macro_ndcg@10": round(mean(r["metrics"]["ndcg@10"] for r in results), 4),
        "macro_precision@5": round(mean(r["metrics"]["precision@5"] for r in results), 4),
        "macro_recall@5": round(mean(r["metrics"]["recall@5"] for r in results), 4),
        "macro_recall@10": round(mean(r["metrics"]["recall@10"] for r in results), 4),
        "macro_recall@20": round(mean(r["metrics"]["recall@20"] for r in results), 4),
        "elapsed_s": round(time.time() - t0, 2),
        "results": results,
    }
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"RANKING_{tag}_{stamp}.json"
    latest = out_dir / f"RANKING_{tag}_latest.json"
    if args.profile == "baseline" and not args.tag:
        path = out_dir / "RANKING_BASELINE_v1.json"
        latest = ROOT / "eval" / "reports" / "RANKING_BASELINE_latest.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = [
        f"# Ranking · {tag}",
        "",
        f"n={report['n']} profile={args.profile}",
        f"- MRR **{report['macro_mrr']}**",
        f"- nDCG@10 **{report['macro_ndcg@10']}**",
        f"- Precision@5 **{report['macro_precision@5']}**",
        f"- post-ranking R@5/10/20 {report['macro_recall@5']} / {report['macro_recall@10']} / {report['macro_recall@20']}",
        "",
        "## Failures (non-ok)",
    ]
    for r in results:
        if r["failure_pattern"] != "ok":
            md.append(
                f"- {r['id']}: {r['failure_pattern']} ranks={r['ranks']} top5={r['retrieved_items'][:5]}"
            )
    md_path = path.with_suffix(".md")
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "results"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", latest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
