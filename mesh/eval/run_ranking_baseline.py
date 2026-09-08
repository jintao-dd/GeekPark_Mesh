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


def _title_bucket(title: str) -> str:
    t = re.sub(r"\s+", "", title or "")
    # 同族挤压：取前若干汉字/字母作 bucket（不改可见范围，只阻尼加成）
    chars = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]", t)
    return "".join(chars[:6]) or "_"


def apply_ranking_v1_1(
    hits: list[dict],
    query: str,
    *,
    scope_meta: dict | None = None,
) -> list[dict]:
    """Ranking v1.1：标题增益非霸权 + term 覆盖率 + 原始位次阻尼 + 同族阻尼。不改召回集合。"""
    terms = [t for t in _query_terms(query) if len(t) >= 2]
    latin = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9_.-]{2,}", query or "")]
    team_hints = [w for w in ("编辑部", "商业化", "社区", "视频号", "选题") if w in (query or "")]
    issue_slug = ((scope_meta or {}).get("context_slug") or (scope_meta or {}).get("slug") or "").strip()
    date_from = (scope_meta or {}).get("date_from") or ""
    date_to = (scope_meta or {}).get("date_to") or ""

    prelim: list[dict] = []
    for orig_i, h0 in enumerate(hits):
        h = dict(h0)
        h["_fts_score"] = float(h0.get("score") or 0)
        title = h.get("title") or ""
        body = h.get("body") or ""
        title_l, body_l = title.lower(), body.lower()
        blob = title + "\n" + body

        hit_terms = sum(1 for t in terms if t in blob)
        coverage = (hit_terms / len(terms)) if terms else 0.0
        latin_hit = sum(1 for w in latin if w.lower() in title_l or w.lower() in body_l)
        latin_cov = (latin_hit / len(latin)) if latin else 0.0
        cov = max(coverage, latin_cov)

        title_bonus = 0.0
        soft = 0.0
        for t in terms:
            if t in title:
                title_bonus += 0.28 if len(t) >= 4 else 0.14
            elif t in body:
                soft += 0.05
        for w in latin:
            wl = w.lower()
            if wl in title_l:
                title_bonus += 0.24
            elif wl in body_l:
                soft += 0.07
        long = max((t for t in terms if re.search(r"[\u4e00-\u9fff]", t)), key=len, default="")
        if long and len(long) >= 4 and long[:4] in title:
            title_bonus += 0.1

        # 低覆盖 → 标题加成大幅衰减（防 R06 单点词噪声霸权）
        title_bonus *= 0.25 + 0.75 * cov
        title_bonus = min(title_bonus, 0.42)

        src = h.get("source") or ""
        if src == "item_entity_facts":
            soft += 0.14 * max(cov, 0.4)
        elif src == "item_facts":
            soft += 0.07 * max(cov, 0.4)

        for th in team_hints:
            if th in title or th in body:
                soft += 0.1
                break

        hit_slug = str(h.get("issue_slug") or h.get("slug") or "")
        if issue_slug and hit_slug and hit_slug == issue_slug:
            soft += 0.06
        hit_date = str(h.get("date_end") or h.get("date") or "")[:10]
        if date_from and date_to and hit_date and date_from[:10] <= hit_date <= date_to[:10]:
            soft += 0.05

        # 原始 FTS 位次阻尼：远处噪声难跃入 Top5；高覆盖深相关仍可升
        if cov >= 0.7:
            pos_scale = max(0.45, 1.0 - 0.025 * orig_i)
        else:
            pos_scale = max(0.15, 1.0 - 0.055 * orig_i)

        bonus = (title_bonus + soft) * pos_scale
        bonus = min(0.58, bonus)
        h["_rank_v11_title"] = title_bonus
        h["_rank_v11_soft"] = soft
        h["_rank_v11_cov"] = cov
        h["_rank_v11_pos_scale"] = pos_scale
        h["_rank_v11_bucket"] = _title_bucket(title)
        h["_rank_v11_raw_bonus"] = bonus
        h["_orig_i"] = orig_i
        prelim.append(h)

    bucket_seen: dict[str, int] = {}
    prelim_by_orig = sorted(prelim, key=lambda x: int(x.get("_orig_i") or 0))
    out = []
    for h in prelim_by_orig:
        b = h["_rank_v11_bucket"]
        n = bucket_seen.get(b, 0)
        bucket_seen[b] = n + 1
        bonus = float(h["_rank_v11_raw_bonus"])
        if n == 1:
            bonus *= 0.55
        elif n >= 2:
            bonus *= 0.3
        h["score"] = float(h["_fts_score"]) - bonus
        h["_rank_v11_bonus"] = bonus
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
    ap.add_argument("--profile", choices=("baseline", "v1", "v1_1"), default="baseline")
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
            hits, info = _retrieve_hits(con, query, row.get("scope") or {})
            if args.profile == "v1":
                hits = apply_ranking_v1(hits, query)
            elif args.profile == "v1_1":
                hits = apply_ranking_v1_1(hits, query, scope_meta=info)
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
