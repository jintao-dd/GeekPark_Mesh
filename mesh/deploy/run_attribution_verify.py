#!/usr/bin/env python3
"""Publish 前 Attribution + Narrative 分离扫描。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.attribution_verify import scan_issue
from app.main import publish_blockers
from app.relation_gate import issue_publish_blockers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    args = ap.parse_args()

    con = db.connect()
    row = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (args.slug,)).fetchone()
    if not row:
        print("issue not found:", args.slug)
        sys.exit(1)

    iid = row["id"]
    draft_json = row["draft_json"] or "{}"
    scan = scan_issue(con, iid, draft_json)
    all_blockers = publish_blockers(con, iid, draft_json)

    print(f"=== Attribution Verify · {args.slug} ===")
    print("stats:", json.dumps(scan.stats, ensure_ascii=False))
    print(f"\nAttribution blockers ({len(scan.blockers)}):")
    if scan.blockers:
        for e in scan.blockers[:50]:
            print(" ", e)
        if len(scan.blockers) > 50:
            print(f"  ... +{len(scan.blockers) - 50} more")
    else:
        print("  (none)")

    print(f"\nNarrative flags ({len(scan.narrative_flags)}) — 不拦截 publish，供 Narrative 层清理:")
    for f in scan.narrative_flags[:25]:
        print(" ", f)
    if len(scan.narrative_flags) > 25:
        print(f"  ... +{len(scan.narrative_flags) - 25} more")

    print(f"\nAll publish_blockers ({len(all_blockers)}):")
    if all_blockers:
        for e in all_blockers:
            print(" ", e)
    else:
        print("  (none — ready for owner confirm)")

    con.close()
    sys.exit(0 if scan.ok and not all_blockers else 1)


if __name__ == "__main__":
    main()
