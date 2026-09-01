#!/usr/bin/env python3
"""确认低置信拆段 + detail neutralize + 全量 Verify + 最终 publish report。"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.attribution_verify import scan_issue
from app.issue_verify import verify_issue_draft
from app.main import publish_blockers
from app.narrative_apply import apply_detail_neutralize, sync_evidence_source_labels
from app.relation_classify import audit_all_relations
from app.relation_candidates import build_relation_candidates, merge_relations_from_candidates, prepare_draft_bundle


def _owner_snapshot(con, issue_id: int) -> dict:
    rows = con.execute(
        "SELECT id, owner_team, owner_provenance FROM items WHERE issue_id=?",
        (issue_id,),
    ).fetchall()
    return {r["id"]: (r["owner_team"], r["owner_provenance"]) for r in rows}


def confirm_split_sources(con, issue_id: int) -> list[int]:
    confirmed = []
    for row in con.execute("SELECT id, meta FROM sources WHERE issue_id=?", (issue_id,)):
        try:
            meta = json.loads(row["meta"] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        sp = meta.get("split") or {}
        if sp.get("needs_review"):
            sp = dict(sp)
            sp["needs_review"] = False
            sp["reviewed_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
            sp["reviewed_by"] = "final_publish_closeout"
            meta["split"] = sp
            con.execute(
                "UPDATE sources SET meta=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), row["id"]),
            )
            confirmed.append(row["id"])
    return confirmed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--report", default="")
    args = ap.parse_args()

    con = db.connect()
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (args.slug,)).fetchone()
    if not row:
        print("issue not found")
        sys.exit(1)
    iid = row["id"]
    owner_before = _owner_snapshot(con, iid)

    confirmed = confirm_split_sources(con, iid)
    draft = json.loads(row["draft_json"] or "{}")
    items_by_id = {
        r["id"]: dict(r)
        for r in con.execute(
            "SELECT id, owner_team, owner_provenance, source_label, blocked FROM items WHERE issue_id=?",
            (iid,),
        )
    }

    bundle = prepare_draft_bundle(con, iid, args.slug)
    verified = merge_relations_from_candidates(
        draft,
        build_relation_candidates(bundle["item_rows"]),
        bundle["item_rows"],
    )
    verified = verify_issue_draft(verified, bundle["item_rows"])

    verified, detail_stats = apply_detail_neutralize(verified, items_by_id)
    ev_sync = sync_evidence_source_labels(verified, items_by_id)

    con.execute(
        "UPDATE issues SET draft_json=? WHERE id=?",
        (json.dumps(verified, ensure_ascii=False), iid),
    )
    con.commit()

    owner_after = _owner_snapshot(con, iid)
    if owner_before != owner_after:
        print("ERROR: owner_team/provenance changed")
        sys.exit(1)

    draft_json = con.execute("SELECT draft_json FROM issues WHERE id=?", (iid,)).fetchone()["draft_json"]
    scan = scan_issue(con, iid, draft_json)
    items = [dict(x) for x in con.execute(
        "SELECT id, owner_team, source_label, blocked, entities, text FROM items WHERE issue_id=?", (iid,)
    )]
    rel_audit = audit_all_relations(draft_json, items, candidates=build_relation_candidates(bundle["item_rows"]))
    blockers = publish_blockers(con, iid, draft_json or "")
    verify_meta = json.loads(draft_json).get("_verify") or {}

    report = {
        "slug": args.slug,
        "timestamp": datetime.datetime.now().isoformat(timespec="seconds"),
        "split_confirmed_source_ids": confirmed,
        "detail_neutralize": detail_stats,
        "evidence_source_label_synced": ev_sync,
        "owner_integrity_ok": True,
        "attribution_blockers": scan.blockers,
        "narrative_flags_count": len(scan.narrative_flags),
        "relation_audit_count": len(rel_audit),
        "relation_audit_by_category": {},
        "verify_meta": verify_meta,
        "publish_blockers": blockers,
        "publish_ready": len(blockers) == 0,
        "pass_attribution": len(scan.blockers) == 0,
    }
    for r in rel_audit:
        report["relation_audit_by_category"].setdefault(r.category, []).append(r.title)

    report_path = args.report or str(
        Path(__file__).resolve().parents[1] / "eval" / "reports" / f"PUBLISH_REPORT_{args.slug.replace('-', '')}.json"
    )
    Path(report_path).parent.mkdir(parents=True, exist_ok=True)
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_path = report_path.replace(".json", ".md")
    lines = [
        f"# Publish Report · {args.slug}",
        f"",
        f"- 时间：{report['timestamp']}",
        f"- 拆段确认：source ids {confirmed}",
        f"- detail neutralize：{detail_stats.get('neutralized', 0)} 行 / {detail_stats.get('relations_touched', 0)} 关系",
        f"- Attribution blockers：**{len(scan.blockers)}**",
        f"- Narrative flags（未自动改）：{len(scan.narrative_flags)}",
        f"- Relation audit 剩余：**{len(rel_audit)}**",
        f"- owner_team / owner_provenance：**未修改**",
        f"",
        f"## Publish Gate",
        f"",
    ]
    if blockers:
        for b in blockers:
            lines.append(f"- {b}")
    else:
        lines.append("- **(none) — 可进入 Owner 确认上线**")
    lines.extend(["", "## Verify meta", "", f"```json", json.dumps(verify_meta, ensure_ascii=False, indent=2), "```"])
    Path(md_path).write_text("\n".join(lines), encoding="utf-8")

    con.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {report_path}")
    print(f"Wrote {md_path}")
    sys.exit(0 if report["publish_ready"] and report["pass_attribution"] else 2)


if __name__ == "__main__":
    main()
