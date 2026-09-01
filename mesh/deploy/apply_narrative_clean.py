#!/usr/bin/env python3
"""正式 apply Narrative cleaner（仅 strip_prefix；不改 owner_team / owner_provenance）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.narrative_clean import clean_source_label


def _owner_snapshot(con, issue_id: int) -> dict[int, tuple]:
    rows = con.execute(
        "SELECT id, owner_team, owner_provenance FROM items WHERE issue_id=?",
        (issue_id,),
    ).fetchall()
    return {r["id"]: (r["owner_team"], r["owner_provenance"]) for r in rows}


def apply_issue(con, issue_id: int, *, dry_run: bool = False) -> dict:
    before = _owner_snapshot(con, issue_id)
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, owner_team, owner_provenance, source_label, source_labels, blocked
               FROM items WHERE issue_id=? AND blocked=0 AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    item_label_map: dict[int, tuple[str, str]] = {}
    stats = {"keep": 0, "strip_prefix": 0, "needs_review": 0, "items_updated": 0}

    for it in items:
        owner = it.get("owner_team") or ""
        r = clean_source_label(it.get("source_label") or "", owner)
        stats[r.action] = stats.get(r.action, 0) + 1
        if r.action != "strip_prefix":
            continue
        item_label_map[it["id"]] = (r.original, r.cleaned)
        if dry_run:
            continue
        labels_json = it.get("source_labels") or "[]"
        try:
            labels = json.loads(labels_json) if isinstance(labels_json, str) else list(labels_json or [])
        except (json.JSONDecodeError, TypeError):
            labels = []
        new_labels = [r.cleaned if x == r.original else x for x in labels]
        if not labels and r.cleaned:
            new_labels = [r.cleaned]
        con.execute(
            "UPDATE items SET source_label=?, source_labels=? WHERE id=?",
            (r.cleaned, json.dumps(new_labels, ensure_ascii=False), it["id"]),
        )
        stats["items_updated"] += 1

    draft_row = con.execute("SELECT draft_json FROM issues WHERE id=?", (issue_id,)).fetchone()
    draft = json.loads(draft_row["draft_json"] or "{}") if draft_row else {}
    draft_changed = False
    ev_updated = 0

    for rel in draft.get("relations") or []:
        for ev in rel.get("evidence") or []:
            iid = ev.get("item_id")
            if iid not in item_label_map:
                continue
            _, cleaned = item_label_map[iid]
            if ev.get("source_label") != cleaned:
                ev["source_label"] = cleaned
                draft_changed = True
                ev_updated += 1

    if draft_changed and not dry_run:
        con.execute(
            "UPDATE issues SET draft_json=? WHERE id=?",
            (json.dumps(draft, ensure_ascii=False), issue_id),
        )

    if not dry_run:
        con.commit()
        after = _owner_snapshot(con, issue_id)
        if before != after:
            raise RuntimeError("owner_team / owner_provenance 被意外修改，已 abort（请检查）")

    stats["evidence_updated"] = ev_updated
    stats["draft_json_updated"] = draft_changed
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    con = db.connect()
    row = con.execute("SELECT id FROM issues WHERE slug=?", (args.slug,)).fetchone()
    if not row:
        print("issue not found:", args.slug)
        sys.exit(1)

    stats = apply_issue(con, row["id"], dry_run=args.dry_run)
    con.close()
    print(json.dumps({"slug": args.slug, "dry_run": args.dry_run, **stats}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
