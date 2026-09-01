#!/usr/bin/env python3
"""Phase 3 验收：tmesh 上重跑 verify + 打印 narrative→evidence→item→source 链（默认不 publish）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.issue_verify import issue_field_inventory, verify_issue_draft
from app.relation_candidates import merge_relations_from_candidates, prepare_draft_bundle
from app.relation_gate import issue_publish_blockers


def _trace_relation(rel: dict, items_by_id: dict) -> dict:
    title = rel.get("title") or ""
    chain = {"title": title, "narrative": {"body": rel.get("body"), "details": rel.get("details") or []}, "evidence": []}
    for e in rel.get("evidence") or []:
        iid = e.get("item_id")
        row = items_by_id.get(iid) or {}
        chain["evidence"].append({
            "snippet": e.get("snippet"),
            "item_id": iid,
            "source_id": e.get("source_id") or row.get("source_id"),
            "team": e.get("team"),
            "pointer": e.get("pointer") or row.get("pointer"),
            "source_label": e.get("source_label") or row.get("source_label"),
            "item_text_head": (row.get("text") or "")[:120],
        })
    return chain


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--sample", type=int, default=3, help="随机展示 N 张关系卡回溯链")
    args = ap.parse_args()

    con = db.connect()
    issue = con.execute("SELECT * FROM issues WHERE slug=?", (args.slug,)).fetchone()
    if not issue:
        print("issue not found", args.slug)
        sys.exit(1)
    iid = issue["id"]
    bundle = prepare_draft_bundle(con, iid, args.slug)
    raw = issue["draft_json"] or issue["published_json"] or "{}"
    draft = json.loads(raw)
    verified = merge_relations_from_candidates(draft, bundle["relation_candidates"], bundle["item_rows"])
    verified = verify_issue_draft(verified, bundle["item_rows"])

    items_by_id = {x["id"]: x for x in bundle["item_rows"]}
    blockers = issue_publish_blockers(verified, bundle["item_rows"])

    print("=== Phase 3 field inventory ===")
    for row in issue_field_inventory():
        print(f"  {row['field']}: {row['evidence']}")

    print("\n=== Verify meta ===")
    print(json.dumps(verified.get("_verify") or {}, ensure_ascii=False, indent=2))

    print("\n=== Publish blockers (dry-run) ===")
    if blockers:
        for e in blockers:
            print(" ", e)
    else:
        print("  (none — still requires owner confirm + typed 上线)")

    rels = verified.get("relations") or []
    sample = [r for r in rels if r.get("evidence")][: max(0, args.sample)]
    print(f"\n=== Sample chains ({len(sample)} relations with evidence) ===")
    for i, rel in enumerate(sample, 1):
        print(json.dumps(_trace_relation(rel, items_by_id), ensure_ascii=False, indent=2))
        if i < len(sample):
            print("---")

    con.close()


if __name__ == "__main__":
    main()
