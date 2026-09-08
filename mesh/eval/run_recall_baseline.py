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


def _load_gold() -> list[dict]:
    rows = []
    for line in GOLD_PATH.read_text(encoding="utf-8").splitlines():
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
    seen = set()
    for h in hits or []:
        iid = h.get("item_id")
        if iid is None or str(iid).strip() == "":
            continue
        s = str(int(iid)) if str(iid).isdigit() else str(iid)
        if s in seen:
            continue
        seen.add(s)
        ids.append(s)
        if len(ids) >= top_n:
            break
    info = {
        "mode": mode,
        "slug": slug,
        "date_from": date_from,
        "date_to": date_to,
        "n_hits_raw": len(hits or []),
        "used_vector": bool((meta or {}).get("used_vector")),
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
    args = ap.parse_args()

    _disable_embed()
    from app import db

    gold = _load_gold()
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
            print(f"[{i}/{len(gold)}] {qid} {query[:36]}…", flush=True)
            t1 = time.time()
            try:
                retrieved, info = _retrieve_item_ids(con, query, row.get("scope") or {})
            except Exception as e:
                retrieved, info = [], {"error": str(e)}
            elapsed = int((time.time() - t1) * 1000)
            missed = [x for x in relevant if x not in set(retrieved)]
            hit = [x for x in relevant if x in set(retrieved)]
            metrics = {f"recall@{k}": round(_recall_at(rel_set, retrieved, k), 4) for k in KS}
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
                    "metrics": metrics,
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
    out = {
        "phase": "Retrieval Recall Phase 0 Baseline",
        "gold": GOLD_PATH.name,
        "n": len(results),
        "embed": os.environ.get("MESH_RECALL_USE_EMBED", "").strip() in ("1", "true", "yes"),
        "macro_recall@5": mean_recall(5),
        "macro_recall@10": mean_recall(10),
        "macro_recall@20": mean_recall(20),
        "failure_pattern_hist": dict(patterns),
        "elapsed_s": round(time.time() - t0, 1),
        "completion_criteria": {
            "gold_built": True,
            "baseline_ran": True,
            "all_pass_required": False,
            "forbidden": [
                "no RAG change",
                "no Chunk/FTS/Vector/Rewrite",
                "no Rerank/Ranking",
            ],
            "next": "review failure patterns before any retrieval change",
        },
        "results": results,
    }

    stamp = time.strftime("%Y%m%d_%H%M%S")
    report = ROOT / "eval" / "reports" / f"RECALL_BASELINE_{stamp}.json"
    latest = ROOT / "eval" / "reports" / "RECALL_BASELINE_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    report.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    md = ROOT / "eval" / "reports" / "RECALL_BASELINE_latest.md"
    lines = [
        "# Retrieval Recall Phase 0 · Baseline",
        "",
        f"- Gold: `{GOLD_PATH.name}` · n={out['n']}",
        f"- Macro Recall@5 / @10 / @20 = "
        f"**{out['macro_recall@5']:.0%}** / **{out['macro_recall@10']:.0%}** / "
        f"**{out['macro_recall@20']:.0%}**",
        f"- Embed: {out['embed']} · Elapsed: {out['elapsed_s']}s",
        "",
        "> 完成条件：Gold + Baseline + failure pattern。**不要求抬分。禁止改 RAG。**",
        "",
        "## Failure pattern histogram",
        "",
    ]
    for k, v in patterns.most_common():
        lines.append(f"- `{k}`: {v}")
    lines += ["", "## Per-item", ""]
    for r in results:
        lines.append(
            f"- **{r['id']}** [{r['category']}] "
            f"R@5={r['metrics']['recall@5']:.2f} "
            f"R@10={r['metrics']['recall@10']:.2f} "
            f"R@20={r['metrics']['recall@20']:.2f} · "
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
                "n": out["n"],
                "macro_recall@5": out["macro_recall@5"],
                "macro_recall@10": out["macro_recall@10"],
                "macro_recall@20": out["macro_recall@20"],
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
