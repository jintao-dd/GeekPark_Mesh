#!/usr/bin/env python3
"""对 Relation blockers 分类：补 evidence / 降级 weak / block。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.relation_candidates import build_relation_candidates, prepare_draft_bundle
from app.relation_classify import (
    ACTION_ADD_EVIDENCE,
    ACTION_BLOCK,
    ACTION_DOWNGRADE_WEAK,
    CAT_LABELS,
    audit_all_relations,
    classify_blocked_relations,
    summarize_classifications,
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--audit-all", action="store_true", help="含叙事/弱关系风险的全量审计")
    args = ap.parse_args()

    con = db.connect()
    issue = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (args.slug,)).fetchone()
    if not issue:
        print("issue not found:", args.slug)
        sys.exit(1)

    iid = issue["id"]
    bundle = prepare_draft_bundle(con, iid, args.slug)
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, owner_provenance, blocked, text, entities,
                      source_label, pointer FROM items WHERE issue_id=?""",
            (iid,),
        )
    ]
    cands = build_relation_candidates(bundle["item_rows"])
    classify_fn = audit_all_relations if args.audit_all else classify_blocked_relations
    rows = classify_fn(issue["draft_json"], items, candidates=cands)
    summary = summarize_classifications(rows)

    if args.json:
        print(json.dumps({
            "slug": args.slug,
            "summary": summary,
            "relations": [
                {
                    "title": r.title,
                    "category": r.category,
                    "category_label": CAT_LABELS.get(r.category, r.category),
                    "action": r.recommended_action,
                    "reason": r.reason,
                    "missing_teams": r.missing_teams,
                    "supported_teams": r.supported_teams,
                    "weak": r.weak,
                    "label": r.label,
                    "provenance_ok": r.provenance_ok,
                    "evidence_count": r.evidence_count,
                    "team_gaps": [
                        {
                            "team": g.team,
                            "text_match": g.has_text_match,
                            "entity_match": g.has_entity_match,
                            "in_details": g.in_details,
                            "in_evidence": g.in_evidence,
                            "item_ids": g.item_ids[:5],
                        }
                        for g in r.team_gaps
                    ],
                }
                for r in rows
            ],
        }, ensure_ascii=False, indent=2))
        con.close()
        return

    print(f"=== Relation Blocker 分类 · {args.slug} ===")
    print(f"共 {summary['total']} 条关系触发 team blocker\n")

    for cat, label in CAT_LABELS.items():
        titles = summary["by_category"].get(label, [])
        if not titles:
            continue
        print(f"## {label} ({len(titles)})")
        for r in rows:
            if r.category != cat:
                continue
            print(f"  · {r.title}")
            print(f"    动作: {r.recommended_action} | 缺: {r.missing_teams} | 有: {r.supported_teams}")
            print(f"    {r.reason}")
        print()

    print("## 建议动作汇总")
    action_names = {
        ACTION_ADD_EVIDENCE: "补 Evidence / 修 entity",
        ACTION_DOWNGRADE_WEAK: "降级 weak / 删 narrative detail",
        ACTION_BLOCK: "直接 block / 删除",
    }
    for act, titles in summary["by_action"].items():
        print(f"  {action_names.get(act, act)}: {len(titles)} 条")

    con.close()


if __name__ == "__main__":
    main()
