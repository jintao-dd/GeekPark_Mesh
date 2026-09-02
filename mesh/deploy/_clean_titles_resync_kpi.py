#!/usr/bin/env python3
"""Clean route-tail copy in title/body/details and resync KPI + published."""
from __future__ import annotations

import json
import sys
from datetime import datetime

from app import db
from app.relation_display import attach_reader_flags, split_relations_for_publish
from app.relation_writer import strip_route_meta_copy


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json, status FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    draft = json.loads(row["draft_json"] or "{}")
    samples = []
    cleaned = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        nr = dict(r)
        before_t = nr.get("title") or ""
        before_b = nr.get("body") or ""
        before_d = [str(d).strip() for d in (nr.get("details") or []) if str(d).strip()]
        nr["title"] = strip_route_meta_copy(before_t)
        nr["body"] = strip_route_meta_copy(before_b)
        nr["details"] = [strip_route_meta_copy(d) for d in before_d]
        nr["details"] = [d for d in nr["details"] if d]
        changed = (
            nr["title"] != before_t
            or nr["body"] != before_b
            or nr["details"] != before_d
        )
        if changed:
            samples.append({
                "before_title": before_t,
                "after_title": nr["title"],
                "before_body": before_b,
                "after_body": nr["body"],
                "before_details0": before_d[0] if before_d else "",
                "after_details0": nr["details"][0] if nr["details"] else "",
            })
        cleaned.append(nr)

    rels = attach_reader_flags(cleaned)
    reader, backlog = split_relations_for_publish(rels)
    draft["relations"] = rels
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    kpis = list(draft.get("kpis") or [])
    for k in kpis:
        if k.get("label") == "可同步的关系":
            k["n"] = str(len(rels))
    if not any(k.get("label") == "可同步的关系" for k in kpis):
        kpis.insert(0, {"n": str(len(rels)), "label": "可同步的关系"})
    draft["kpis"] = kpis

    pub = dict(draft)
    pub["relations"] = reader

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    payload_draft = json.dumps(draft, ensure_ascii=False)
    payload_pub = json.dumps(pub, ensure_ascii=False)
    if row["status"] == "published":
        con.execute(
            "UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?",
            (payload_draft, payload_pub, stamp, int(row["id"])),
        )
        # 文案清理不重跑 embedding 索引（易超时）；读者页读 published_json 即可
    else:
        con.execute(
            "UPDATE issues SET draft_json=?, updated_at=? WHERE id=?",
            (payload_draft, stamp, int(row["id"])),
        )
    con.execute(
        "INSERT INTO edits(issue_id,user,target,before,after) VALUES(?,?,?,?,?)",
        (
            int(row["id"]),
            "deploy-fix",
            "route_meta_copy",
            "…用得上/可对接/可承接/可用",
            f"cleaned {len(samples)}; kpi={len(rels)}",
        ),
    )
    with db.write_lock():
        db.commit_retry(con)
    con.close()
    print(json.dumps({
        "ok": True,
        "slug": slug,
        "status": row["status"],
        "n_relations": len(rels),
        "kpi": str(len(rels)),
        "n_cleaned": len(samples),
        "samples": samples[:12],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
