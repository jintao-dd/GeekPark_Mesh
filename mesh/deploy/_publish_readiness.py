#!/usr/bin/env python3
"""Publish readiness checklist for one issue."""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

from app import db
from app.relation_display import reader_visible

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
META = re.compile(r"记录标注.*?用得上|明确标注.*?用得上")


def kpi(data, label="可同步的关系"):
    for k in data.get("kpis") or []:
        if k.get("label") == label:
            return k.get("n")
    return None


def main() -> None:
    con = db.connect()
    r = con.execute(
        "SELECT id, status, period_label, draft_json, published_json, updated_at FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not r:
        raise SystemExit(f"missing {slug}")
    d = json.loads(r["draft_json"] or "{}")
    p = json.loads(r["published_json"] or "{}") if r["published_json"] else {}
    con.close()

    rels = [x for x in (d.get("relations") or []) if isinstance(x, dict)]
    meta_hits = []
    issues = []
    for x in rels:
        blob = " ".join([
            str(x.get("title") or ""),
            str(x.get("body") or ""),
            " ".join(x.get("details") or []),
        ])
        if META.search(blob):
            meta_hits.append(x.get("title"))
        if not (x.get("evidence") or []):
            issues.append({"type": "no_evidence", "title": x.get("title")})
        if not (x.get("body") or "").strip():
            issues.append({"type": "no_body", "title": x.get("title")})
        if not x.get("decision_tier"):
            issues.append({"type": "no_tier", "id": x.get("candidate_id"), "title": x.get("title")})

    reader = [x for x in rels if reader_visible(x)]
    print(json.dumps({
        "slug": slug,
        "period": r["period_label"],
        "status": r["status"],
        "updated_at": str(r["updated_at"]),
        "draft_kpi_relations": kpi(d),
        "published_kpi_relations": kpi(p),
        "n_draft_relations": len(rels),
        "n_published_relations": len(p.get("relations") or []),
        "n_reader_visible": len(reader),
        "label_counts": dict(Counter(x.get("label") for x in rels)),
        "tier_counts": dict(Counter((x.get("decision_tier") or "null") for x in rels)),
        "n_with_evidence": sum(1 for x in rels if x.get("evidence")),
        "n_meta_route_copy_left": len(meta_hits),
        "meta_hits": meta_hits,
        "structural_issues": issues,
        "reader_titles": [x.get("title") for x in reader],
        "has_lead": bool((d.get("lead") or "").strip()),
        "audit": (d.get("_relation_decision_audit") or {}).get("outcome_summary"),
        "verdict_hints": {
            "kpi_ok": kpi(d) not in (None, "0", 0),
            "all_grounded": not any(i["type"] == "no_evidence" for i in issues),
            "meta_copy_clean": len(meta_hits) == 0,
            "still_draft": r["status"] == "draft",
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
