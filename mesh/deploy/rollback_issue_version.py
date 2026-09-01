#!/usr/bin/env python3
"""回滚 issue 到 versions 快照（生产恢复用）。

用法（容器内，只读记录 + 回滚）：
  python deploy/rollback_issue_version.py --slug 2026-8-17 --version v1
  python deploy/rollback_issue_version.py --slug 2026-8-17 --version v1 --dry-run
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db


def _rel_stats(data: dict) -> dict:
    rels = data.get("relations") or []
    return {
        "n_relations": len(rels),
        "n_weak": sum(1 for r in rels if r.get("weak")),
        "n_with_evidence": sum(1 for r in rels if r.get("evidence")),
    }


def rollback(con, slug: str, version: str, *, dry_run: bool = False) -> dict:
    row = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    iid = row["id"]
    cur_pub = json.loads(row["published_json"] or "{}")
    before = {
        "published_at": row["published_at"],
        "updated_at": row["updated_at"],
        **_rel_stats(cur_pub),
    }
    ver = con.execute(
        "SELECT version, at, snapshot_json FROM versions WHERE issue_id=? AND version=?",
        (iid, version),
    ).fetchone()
    if not ver or not ver["snapshot_json"]:
        raise SystemExit(f"version {version} not found for {slug}")
    snap = json.loads(ver["snapshot_json"])
    after_stats = _rel_stats(snap)
    target_at = ver["at"]
    report = {
        "slug": slug,
        "version": version,
        "dry_run": dry_run,
        "before": before,
        "after_expected": {**after_stats, "published_at": target_at},
    }
    if dry_run:
        return report

    payload = json.dumps(snap, ensure_ascii=False)
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        """UPDATE issues SET published_json=?, draft_json=?, status='published',
           published_at=?, updated_at=? WHERE id=?""",
        (payload, payload, target_at, now, iid),
    )
    db.register_entities(con, snap, slug)
    db.snapshot_published_items(con, iid)
    db.reindex_issue(con, iid)
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (iid, "rollback", "publish", json.dumps(before, ensure_ascii=False),
         f"rollback to {version} ({target_at})"),
    )
    con.commit()

    chk = con.execute("SELECT published_json, published_at FROM issues WHERE id=?", (iid,)).fetchone()
    restored = json.loads(chk["published_json"])
    report["after_actual"] = {**_rel_stats(restored), "published_at": chk["published_at"]}
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    con = db.connect()
    try:
        report = rollback(con, args.slug, args.version, dry_run=args.dry_run)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
