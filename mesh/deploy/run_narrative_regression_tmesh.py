#!/usr/bin/env python3
"""tmesh 上 Narrative apply 后回归（Verify + publish gate）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.attribution_verify import scan_issue
from app.main import publish_blockers
from app.relation_classify import audit_all_relations
from app.relation_candidates import build_relation_candidates, prepare_draft_bundle


def main():
    slug = "2026-8-17"
    con = db.connect()
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    iid = row["id"]

    # owner 未动
    bad_owner = con.execute(
        """SELECT COUNT(*) c FROM items WHERE issue_id=? AND blocked=0
           AND (owner_team IS NULL OR owner_team='' OR owner_provenance IS NULL OR owner_provenance='')""",
        (iid,),
    ).fetchone()["c"]

    scan = scan_issue(con, iid, row["draft_json"])
    bundle = prepare_draft_bundle(con, iid, slug)
    items = [
        dict(x)
        for x in con.execute(
            "SELECT id, owner_team, source_label, blocked, entities, text FROM items WHERE issue_id=?",
            (iid,),
        )
    ]
    rel_audit = audit_all_relations(row["draft_json"], items, candidates=build_relation_candidates(bundle["item_rows"]))
    blockers = publish_blockers(con, iid, row["draft_json"] or "")

    report = {
        "slug": slug,
        "owner_integrity": {"empty_owner_or_provenance": bad_owner},
        "attribution_blockers": scan.blockers,
        "attribution_blocker_count": len(scan.blockers),
        "narrative_flags": len(scan.narrative_flags),
        "narrative_flag_samples": scan.narrative_flags[:8],
        "relation_audit_count": len(rel_audit),
        "relation_audit_by_cat": {},
        "publish_blockers": blockers,
        "publish_blocker_count": len(blockers),
        "pass_attribution": len(scan.blockers) == 0 and bad_owner == 0,
    }
    for r in rel_audit:
        report["relation_audit_by_cat"].setdefault(r.category, []).append(r.title)

    con.close()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    sys.exit(0 if report["pass_attribution"] else 1)


if __name__ == "__main__":
    main()
