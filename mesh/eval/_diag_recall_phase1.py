#!/usr/bin/env python3
"""Recall Phase 1 · ③ 诊断：missed items 在哪一层丢失（只读）。

对每题跑：原 query → FTS / item_facts / hybrid / 关键词探针。
不改实现。
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CASES = [
    {
        "id": "R11",
        "query": "Agent 治理与合规 7 月 15 日",
        "slug": "2026-8-17",
        "expected": ["4105", "4106"],
        "focus": ["4106"],
        "probes": ["治理", "合规", "7月15", "Agent 治理", "4106"],
    },
    {
        "id": "R12",
        "query": "端侧模型与智能座舱",
        "slug": "2026-8-17",
        "expected": ["4116", "4120", "4125", "4128"],
        "focus": ["4125"],
        "probes": ["端侧", "座舱", "端侧模型", "智能座舱"],
    },
    {
        "id": "R13",
        "query": "具身智能数据圆桌讨论了什么",
        "slug": "2026-8-17",
        "expected": ["4154", "4155", "4156"],
        "focus": ["4154", "4155", "4156"],
        "probes": ["具身智能", "数据圆桌", "圆桌", "具身"],
    },
    {
        "id": "R18",
        "query": "Founder Park 下半年规划",
        "slug": "2026-8-17",
        "expected": ["4358", "4271"],
        "focus": ["4271"],
        "probes": ["Founder Park", "下半年", "涨粉", "传播复盘", "AGI"],
    },
    {
        "id": "R14",
        "query": "视频号本周播放较好的片子",
        "slug": "2026-09-08",
        "expected": ["4408", "4409", "4410", "4411", "4412"],
        "focus": ["4408", "4409", "4410", "4411", "4412"],
        "probes": ["视频号 播放", "播放", "视频号", "片子", "较好"],
    },
]


def _ids(hits) -> list[str]:
    out = []
    seen = set()
    for h in hits or []:
        iid = h.get("item_id")
        if iid is None:
            continue
        s = str(int(iid)) if str(iid).isdigit() else str(iid)
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _item_snip(con, iid: int) -> dict:
    r = con.execute(
        "SELECT id, issue_id, stype, owner_team, team, substr(text,1,180) AS snip "
        "FROM items WHERE id=%s",
        (iid,),
    ).fetchone()
    if not r:
        return {"exists": False}
    iss = con.execute("SELECT slug FROM issues WHERE id=%s", (r["issue_id"],)).fetchone()
    return {
        "exists": True,
        "slug": iss["slug"] if iss else "",
        "stype": r["stype"],
        "team": r["owner_team"] or r["team"],
        "snip": r["snip"],
    }


def _lane(con, q: str, slug: str, *, date_from=None, date_to=None, limit=40) -> dict:
    from app import search, item_facts, tokenize as tok
    from app.ask_scope import AskScope
    from app import retriever, embeddings

    match_q = tok.build_match_query(q)
    terms = tok.query_terms(q)
    fts = search.fts_search(con, q, slug=slug, limit=limit, date_from=date_from, date_to=date_to)
    items = item_facts.search(
        con, q, slug=slug, date_from=date_from, date_to=date_to, limit=limit // 2 + 8
    )
    hybrid = search.merge_hits(fts, items, limit=limit)

    # full retriever path (embed off)
    embeddings.embed_one = lambda _t: []  # type: ignore
    embeddings.is_configured = lambda: False  # type: ignore
    scope = AskScope(
        channel="harness", slug=slug, team="", date_from=date_from, date_to=date_to, role="viewer"
    )
    mode, hits, meta = retriever.retrieve(con, q, scope, intent=None, query_vec=None)

    return {
        "match_q": match_q,
        "terms": terms,
        "fts_ids": _ids([{**h, "item_id": h.get("item_id")} for h in fts]),
        "fts_n": len(fts or []),
        "item_facts_ids": _ids(items),
        "item_facts_n": len(items or []),
        "merge_ids": _ids(hybrid),
        "retriever_ids": _ids(hits)[:20],
        "retriever_mode": mode,
        "retriever_n": len(hits or []),
    }


def main() -> int:
    os.environ.setdefault("MESH_RECALL_USE_EMBED", "0")
    from app import db

    con = db.connect()
    report = {"cases": []}
    for case in CASES:
        slug = case["slug"]
        row = con.execute(
            "SELECT date_start, date_end FROM issues WHERE slug=%s", (slug,)
        ).fetchone()
        df = (row["date_start"] or "").strip() or None if row else None
        dt = (row["date_end"] or "").strip() or None if row else None

        focus_info = {}
        for fid in case["focus"]:
            focus_info[fid] = _item_snip(con, int(fid))

        base = _lane(con, case["query"], slug, date_from=df, date_to=dt)
        want = set(case["expected"])
        focus = set(case["focus"])
        probes = []
        for pq in case["probes"]:
            pl = _lane(con, pq, slug, date_from=df, date_to=dt)
            probes.append(
                {
                    "probe": pq,
                    "match_q": pl["match_q"],
                    "item_facts_hit_focus": sorted(focus & set(pl["item_facts_ids"])),
                    "merge_hit_focus": sorted(focus & set(pl["merge_ids"])),
                    "retriever_hit_focus": sorted(focus & set(pl["retriever_ids"])),
                    "item_facts_ids_top10": pl["item_facts_ids"][:10],
                }
            )

        layer = "unknown"
        if focus & set(base["retriever_ids"]):
            layer = "ok_in_retriever_top20"
        elif focus & set(base["item_facts_ids"]) or focus & set(base["merge_ids"]):
            layer = "hybrid_or_rank_drop"  # in lane but not top20 after enrich?
        elif any(focus & set(p["item_facts_hit_focus"]) for p in probes):
            layer = "query_formulation"  # probes work, original query doesn't
        elif focus & set(base["fts_ids"]):
            layer = "fts_ok_item_miss"
        else:
            # check if any probe finds them
            if any(p["item_facts_hit_focus"] or p["merge_hit_focus"] for p in probes):
                layer = "query_formulation"
            else:
                layer = "token_or_index_mismatch"

        report["cases"].append(
            {
                "id": case["id"],
                "query": case["query"],
                "slug": slug,
                "date_from": df,
                "date_to": dt,
                "expected": case["expected"],
                "focus": case["focus"],
                "focus_items": focus_info,
                "original": {
                    **base,
                    "hit_expected_retriever": sorted(want & set(base["retriever_ids"])),
                    "miss_expected_retriever": sorted(want - set(base["retriever_ids"])),
                    "hit_focus_item_facts": sorted(focus & set(base["item_facts_ids"])),
                    "hit_focus_merge": sorted(focus & set(base["merge_ids"])),
                },
                "probes": probes,
                "likely_layer": layer,
            }
        )

    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    Path("/tmp/RECALL_P1_DIAG.json").write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
