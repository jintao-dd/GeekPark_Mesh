#!/usr/bin/env python3
"""Republish all draft relations to reader page + refresh KPI for one issue."""
from __future__ import annotations

import json
import sys
from datetime import datetime

from app import db
from app.edm import _build_search_links
from app.relation_display import attach_reader_flags, split_relations_for_publish


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    draft = json.loads(row["draft_json"] or "{}")
    rels = attach_reader_flags([r for r in (draft.get("relations") or []) if isinstance(r, dict)])
    reader, backlog = split_relations_for_publish(rels)
    draft["relations"] = rels
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    # KPI = all reader-visible (= all grounded cards now)
    kpis = list(draft.get("kpis") or [])
    for k in kpis:
        if k.get("label") == "可同步的关系":
            k["n"] = str(len(reader))
    draft["kpis"] = kpis

    pub = dict(draft)
    pub["relations"] = reader
    links = _build_search_links(pub)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    con.execute(
        "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
        (
            json.dumps(draft, ensure_ascii=False),
            json.dumps(pub, ensure_ascii=False),
            stamp,
            int(row["id"]),
        ),
    )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (
            int(row["id"]),
            "deploy-fix",
            "reader_relations",
            "strong-only",
            f"all {len(reader)} reader_visible; edm links refreshed",
        ),
    )
    con.commit()
    con.close()
    print(json.dumps({
        "ok": True,
        "slug": slug,
        "n_reader": len(reader),
        "n_backlog": len(backlog),
        "kpi": next((k.get("n") for k in kpis if k.get("label") == "可同步的关系"), None),
        "edm_search_links": [x.get("label") for x in links],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
