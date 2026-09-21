#!/usr/bin/env python3
"""Retrieval Recall Phase 0 · Baseline（只读当前 Retrieval，禁止改 RAG）。

用法：
  PYTHONPATH=. python eval/run_recall_baseline.py --reuse-env-db
  PYTHONPATH=. python eval/run_recall_baseline.py --reuse-env-db --limit 5

指标：Recall@5 / @10 / @20；逐题 relevant / retrieved / missed；failure pattern。
不调 LLM、不改 Chunk/FTS/Vector/Rerank。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD_PATH = ROOT / "eval" / "retrieval_gold_v1.jsonl"
KS = (5, 10, 20)


def _load_gold(path: Path | None = None) -> list[dict]:
    rows = []
    src = path or GOLD_PATH
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def _disable_embed() -> None:
    """Baseline 默认关向量，避免 timeout；可用 MESH_RECALL_USE_EMBED=1 打开对照。"""
    if os.environ.get("MESH_RECALL_USE_EMBED", "").strip() in ("1", "true", "yes"):
        return
    from app import embeddings

    embeddings.embed_one = lambda _t: []  # type: ignore[assignment]
    embeddings.is_configured = lambda: False  # type: ignore[assignment]


def _parse_issue_scope(con, scope: dict, *, query: str = "") -> tuple[str, str | None, str | None, dict]:
    """与 Agent adapters.scope_from_agent 对齐：IssueRef ⊥ TimeWindow。

    Return (retrieval_slug, date_from, date_to, meta).
    """
    from app.agent import temporal as temporal_mod

    raw = (scope or {}).get("issue") or "latest_published"
    gold_window = ((scope or {}).get("window") or "").strip()
    if raw.startswith("explicit:"):
        slug = raw.split(":", 1)[1].strip()
        issue_mode = "explicit"
    elif raw == "latest_published":
        row = con.execute(
            "SELECT slug FROM issues WHERE status='published' "
            "AND published_json IS NOT NULL AND TRIM(published_json) != '' "
            "ORDER BY date_end DESC, id DESC LIMIT 1"
        ).fetchone()
        slug = (row["slug"] if row else "") or ""
        issue_mode = "latest_published"
    else:
        slug = raw
        issue_mode = "explicit"

    issue_date_from = issue_date_to = None
    if slug:
        r = con.execute(
            "SELECT date_start, date_end FROM issues WHERE slug=?", (slug,)
        ).fetchone()
        if r:
            issue_date_from = (r["date_start"] or "").strip() or None
            issue_date_to = (r["date_end"] or "").strip() or None

    # Gold 可标注 window=recent；无标注时按 query 解析
    q = query or ""
    if gold_window == "recent" and "最近" not in q and "近期" not in q:
        # 保证 Gold 语义与产品锁定一致（R23/R24 等）
        q_for_sem = q + " 最近"
    else:
        q_for_sem = q
    sem = temporal_mod.resolve_time_semantics(
        q_for_sem, issue_mode=issue_mode, issue_slug=slug, has_event_time=False
    )
    if gold_window and gold_window != sem.window:
        # 尊重 Gold 标注的 window，重算 filter
        sem.window = gold_window
        sem.filter_mode = temporal_mod.resolve_filter_mode(
            issue_mode=issue_mode, window=gold_window
        )
    retrieval_slug = temporal_mod.retrieval_slug_for_scope(sem, slug)
    date_from, date_to = temporal_mod.apply_time_filter_to_dates(
        sem,
        issue_date_from=issue_date_from,
        issue_date_to=issue_date_to,
        query=q_for_sem if sem.window == "recent" else q,
    )
    meta = {
        "context_slug": slug,
        "issue_mode": issue_mode,
        "filter_mode": sem.filter_mode,
        "window": sem.window,
    }
    return retrieval_slug, date_from, date_to, meta


def _retrieve_item_ids(con, query: str, scope: dict, *, top_n: int = 40) -> tuple[list[str], dict]:
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
    ids: list[str] = []
    chunk_ids: list[str] = []
    # 位次对齐的 chunk 序列（非 chunk 命中占位 None），用于真实 chunk_recall@N
    ranked_chunks: list[str | None] = []
    channel_hist: dict[str, int] = {}
    vector_ranks: list[int] = []
    seen = set()
    for rank, h in enumerate(hits or [], 1):
        src = str(h.get("source") or "fts")
        channel_hist[src] = channel_hist.get(src, 0) + 1
        if src == "vector":
            vector_ranks.append(rank)
        cid = h.get("chunk_id")
        ranked_chunks.append(str(cid) if cid else None)
        if cid and str(cid) not in seen:
            seen.add(str(cid))
            chunk_ids.append(str(cid))
    item_ids: list[str] = []
    seen_item = set()
    for h in hits or []:
        iid = h.get("item_id")
        if iid is None or str(iid).strip() == "":
            continue
        s = str(int(iid)) if str(iid).isdigit() else str(iid)
        if s in seen_item:
            continue
        seen_item.add(s)
        item_ids.append(s)
        if len(item_ids) >= top_n:
            break
    ids = item_ids
    info = {
        "mode": mode,
        "slug": slug,
        "date_from": date_from,
        "date_to": date_to,
        "n_hits_raw": len(hits or []),
        "used_vector": bool((meta or {}).get("used_vector")),
        "chunk_ids": chunk_ids[:top_n],
        "ranked_chunks": ranked_chunks[:top_n],
        "channel_hist": channel_hist,
        "vector_ranks": vector_ranks,
        **scope_meta,
    }
    return ids, info


def _recall_at(relevant: set[str], retrieved: list[str], k: int) -> float:
    if not relevant:
        return 1.0
    got = set(retrieved[:k]) & relevant
    return len(got) / len(relevant)


def _classify_miss(row: dict, info: dict, retrieved: list[str], missed: list[str]) -> str:
    if not missed:
        return "ok"
    scope = (row.get("scope") or {}).get("issue") or ""
    hit = [x for x in (row.get("relevant_items") or []) if x in set(retrieved)]
    # Scope 产品样本：不再记为 issue_scope_latest_miss（正交语义落地后）
    if scope == "latest_published" and missed and not hit:
        if (info or {}).get("filter_mode") == "time_window":
            return "empty_retrieval" if not retrieved else "total_miss"
        return "issue_scope_latest_miss"
    if not retrieved:
        return "empty_retrieval"
    if len(missed) == len(row.get("relevant_items") or []):
        return "total_miss"
    if scope.startswith("explicit:"):
        return "partial_miss_rank_or_fts"
    return "partial_miss"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="")
    ap.add_argument("--gold", default="", help="gold jsonl 路径（默认 retrieval_gold_v1）")
    ap.add_argument("--tag", default="", help="报告标签，用于归档对比")
    args = ap.parse_args()

    _disable_embed()
    from app import db

    gold_path = Path(args.gold) if args.gold else GOLD_PATH
    if not gold_path.is_absolute():
        gold_path = ROOT / gold_path
    gold = _load_gold(gold_path)
    if args.ids.strip():
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        gold = [r for r in gold if r.get("id") in want]
    if args.limit and args.limit > 0:
        gold = gold[: args.limit]

    con = db.connect()
    results: list[dict] = []
    t0 = time.time()
    try:
        for i, row in enumerate(gold, 1):
            qid = row["id"]
            query = row["query"]
            relevant = [str(x) for x in (row.get("relevant_items") or [])]
            rel_set = set(relevant)
            # chunk 级真值（v2 gold 才有；用于验证候选池是否真的召回到正确片段）
            rel_chunks = [str(x) for x in (row.get("relevant_chunks") or [])]
            rel_chunk_set = set(rel_chunks)
            print(f"[{i}/{len(gold)}] {qid} {query[:36]}…", flush=True)
            t1 = time.time()
            try:
                retrieved, info = _retrieve_item_ids(con, query, row.get("scope") or {})
            except Exception as e:
                retrieved, info = [], {"error": str(e)}
            elapsed = int((time.time() - t1) * 1000)
            got_chunks = [str(x) for x in (info.get("chunk_ids") or [])]
            ranked_chunks = list(info.get("ranked_chunks") or [])
            missed = [x for x in relevant if x not in set(retrieved)]
            hit = [x for x in relevant if x in set(retrieved)]
            metrics = {f"recall@{k}": round(_recall_at(rel_set, retrieved, k), 4) for k in KS}
            chunk_metrics: dict[str, float] = {}
            if rel_chunk_set:
                # 真实位次口径：在第 k 位之前（含）找到的 chunk 占比
                for k in KS:
                    got_k = {c for c in ranked_chunks[:k] if c}
                    chunk_metrics[f"chunk_recall@{k}"] = round(
                        len(rel_chunk_set & got_k) / len(rel_chunk_set), 4
                    )
                chunk_metrics["chunk_in_pool"] = round(
                    len(rel_chunk_set & set(got_chunks)) / len(rel_chunk_set), 4
                )
                chunk_metrics["n_ranked_slots"] = float(len(ranked_chunks))
            pattern = _classify_miss(row, info, retrieved, missed)
            # first-hit rank of any relevant
            ranks = [retrieved.index(x) + 1 for x in relevant if x in retrieved]
            results.append(
                {
                    "id": qid,
                    "category": row.get("category"),
                    "query": query,
                    "scope": row.get("scope"),
                    "relevant_items": relevant,
                    "retrieved_items": retrieved[:20],
                    "hit_items": hit,
                    "missed_items": missed,
                    "relevant_chunks": rel_chunks,
                    "retrieved_chunks": got_chunks[:20],
                    "metrics": metrics,
                    "chunk_metrics": chunk_metrics,
                    "channel_hist": info.get("channel_hist") or {},
                    "vector_ranks": info.get("vector_ranks") or [],
                    "first_relevant_rank": min(ranks) if ranks else None,
                    "failure_pattern": pattern,
                    "retrieval": info,
                    "elapsed_ms": elapsed,
                    "notes": row.get("notes"),
                }
            )
            print(
                f"  → R@5={metrics['recall@5']:.2f} R@10={metrics['recall@10']:.2f} "
                f"R@20={metrics['recall@20']:.2f} miss={missed or '-'} "
                f"pat={pattern} {elapsed}ms",
                flush=True,
            )
    finally:
        con.close()

    def mean_recall(k: int) -> float:
        if not results:
            return 0.0
        return round(
            sum(r["metrics"][f"recall@{k}"] for r in results) / len(results), 4
        )

    patterns = Counter(r["failure_pattern"] for r in results)

    def mean_chunk(k: str) -> float:
        vals = [
            r["chunk_metrics"][k]
            for r in results
            if r.get("chunk_metrics") and k in r["chunk_metrics"]
        ]
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    channel_total: Counter = Counter()
    for r in results:
        for k, v in (r.get("channel_hist") or {}).items():
            channel_total[k] += v

    out = {
        "phase": "Retrieval Recall Baseline",
        "gold": gold_path.name,
        "tag": args.tag or "",
        "n": len(results),
        "n_with_chunk_truth": sum(1 for r in results if r.get("relevant_chunks")),
        "embed": os.environ.get("MESH_RECALL_USE_EMBED", "").strip() in ("1", "true", "yes"),
        "macro_recall@5": mean_recall(5),
        "macro_recall@10": mean_recall(10),
        "macro_recall@20": mean_recall(20),
        "macro_chunk_recall@5": mean_chunk("chunk_recall@5"),
        "macro_chunk_recall@10": mean_chunk("chunk_recall@10"),
        "macro_chunk_recall@20": mean_chunk("chunk_recall@20"),
        "macro_chunk_in_pool": mean_chunk("chunk_in_pool"),
        # 通道分布：P1 验收「向量是否进入候选池」的量化口径
        "channel_hist_total": dict(channel_total),
        "n_cases_with_vector": sum(1 for r in results if r.get("vector_ranks")),
        "mean_vector_rank": (
            round(
                sum(sum(r["vector_ranks"]) for r in results if r.get("vector_ranks"))
                / max(1, sum(len(r["vector_ranks"]) for r in results)),
                2,
            )
            if any(r.get("vector_ranks") for r in results)
            else None
        ),
        "failure_pattern_hist": dict(patterns),
        "elapsed_s": round(time.time() - t0, 1),
        "results": results,
    }

    stamp = time.strftime("%Y%m%d_%H%M%S")
    tag = args.tag or "baseline"
    report = ROOT / "eval" / "reports" / f"RECALL_{tag}_{stamp}.json"
    latest = ROOT / "eval" / "reports" / f"RECALL_{tag}_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    report.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    md = ROOT / "eval" / "reports" / f"RECALL_{tag}_latest.md"
    lines = [
        f"# Retrieval Recall · {tag}",
        "",
        f"- Gold: `{gold_path.name}` · n={out['n']} "
        f"(chunk 真值 {out['n_with_chunk_truth']} 题)",
        f"- Macro Recall@5 / @10 / @20 = "
        f"**{out['macro_recall@5']:.0%}** / **{out['macro_recall@10']:.0%}** / "
        f"**{out['macro_recall@20']:.0%}**",
        f"- Macro Chunk-Recall@5 / @10 / @20 = "
        f"**{out['macro_chunk_recall@5']:.0%}** / **{out['macro_chunk_recall@10']:.0%}** / "
        f"**{out['macro_chunk_recall@20']:.0%}** · 候选池内 {out['macro_chunk_in_pool']:.0%}",
        f"- Embed: {out['embed']} · Elapsed: {out['elapsed_s']}s",
        "",
        "## Failure pattern histogram",
        "",
    ]
    for k, v in patterns.most_common():
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Per-item", ""]
    for r in results:
        cm = r.get("chunk_metrics") or {}
        chunk_bit = (
            f" · CR@5={cm.get('chunk_recall@5', 0):.2f}"
            f" pool={cm.get('chunk_in_pool', 0):.2f}"
            if cm
            else ""
        )
        lines.append(
            f"- **{r['id']}** [{r['category']}] "
            f"R@5={r['metrics']['recall@5']:.2f} "
            f"R@10={r['metrics']['recall@10']:.2f} "
            f"R@20={r['metrics']['recall@20']:.2f}{chunk_bit} · "
            f"`{r['failure_pattern']}`"
        )
        lines.append(f"  - Q: {r['query']}")
        lines.append(f"  - expected: {r['relevant_items']}")
        lines.append(f"  - retrieved[:10]: {r['retrieved_items'][:10]}")
        lines.append(f"  - missed: {r['missed_items'] or '-'}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "tag": tag,
                "gold": gold_path.name,
                "n": out["n"],
                "macro_recall@5": out["macro_recall@5"],
                "macro_recall@10": out["macro_recall@10"],
                "macro_recall@20": out["macro_recall@20"],
                "macro_chunk_recall@5": out["macro_chunk_recall@5"],
                "macro_chunk_recall@10": out["macro_chunk_recall@10"],
                "macro_chunk_recall@20": out["macro_chunk_recall@20"],
                "macro_chunk_in_pool": out["macro_chunk_in_pool"],
                "failure_pattern_hist": out["failure_pattern_hist"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"wrote {report}")
    print(f"wrote {latest}")
    print(f"wrote {md}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
