#!/usr/bin/env python3
"""Inspect KPIs + relation evidence grounding for one slug."""
from __future__ import annotations

import json
import sys
from collections import Counter

from app import db


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json, published_json, status FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    draft = json.loads(row["draft_json"] or "{}")
    pub = json.loads(row["published_json"] or "{}") if row["published_json"] else {}
    con.close()

    def _kpi(data):
        for k in data.get("kpis") or []:
            if k.get("label") == "可同步的关系":
                return k.get("n")
        return None

    rels = [r for r in (draft.get("relations") or []) if isinstance(r, dict)]
    reader = draft.get("_relations_reader") or []
    backlog = draft.get("_relations_backlog") or []

    evidence_audit = []
    for r in rels:
        ev = r.get("evidence") or []
        teams = r.get("teams") or []
        solid = [t for t in teams if not str(t).strip().startswith(("→", "->"))]
        arrow = [t for t in teams if str(t).strip().startswith(("→", "->"))]
        details = r.get("details") or []
        body = (r.get("body") or "").strip()
        title = (r.get("title") or "").strip()
        label = (r.get("label") or "").strip()
        ok = bool(ev) and bool(body) and bool(title) and bool(label)
        evidence_audit.append({
            "title": title,
            "label": label,
            "tier": r.get("decision_tier"),
            "reader_visible": r.get("reader_visible"),
            "n_evidence": len(ev),
            "evidence_teams": [e.get("team") for e in ev if isinstance(e, dict)],
            "solid_teams": solid,
            "arrow_teams": arrow,
            "n_details": len(details),
            "has_body": bool(body),
            "grounded_ok": ok,
            "weak": r.get("weak"),
            "candidate_id": r.get("candidate_id"),
        })

    print(json.dumps({
        "slug": slug,
        "status": row["status"],
        "draft_kpi_relations": _kpi(draft),
        "published_kpi_relations": _kpi(pub),
        "n_draft_relations": len(rels),
        "n_reader": len(reader),
        "n_backlog": len(backlog),
        "n_published_relations": len(pub.get("relations") or []),
        "draft_kpis": draft.get("kpis"),
        "published_kpis": pub.get("kpis"),
        "label_counts": dict(Counter(r.get("label") for r in rels)),
        "n_grounded_ok": sum(1 for x in evidence_audit if x["grounded_ok"]),
        "n_ungrounded": sum(1 for x in evidence_audit if not x["grounded_ok"]),
        "relations": evidence_audit,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
