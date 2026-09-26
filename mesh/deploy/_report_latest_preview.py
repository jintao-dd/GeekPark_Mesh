#!/usr/bin/env python3
"""拉取当期 published_json 关系卡摘要。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    row = con.execute(
        "SELECT slug, status, updated_at, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    con.close()
    if not row:
        print(json.dumps({"error": "not found"}))
        return
    pub = json.loads(row["published_json"] or "{}")
    draft = json.loads(row["draft_json"] or "{}")
    rels = pub.get("relations") or []
    kpis = pub.get("kpis") or []
    kpi_n = next((x.get("n") for x in kpis if x.get("label") == "可同步的关系"), "?")
    cards = []
    for i, r in enumerate(rels):
        teams = r.get("teams") or []
        cards.append({
            "i": i,
            "title": r.get("title"),
            "label": r.get("label"),
            "weak": bool(r.get("weak")),
            "needs_review": bool(r.get("needs_review")),
            "solid_teams": [t for t in teams if not str(t).startswith("→")],
            "suggested_teams": [t for t in teams if str(t).startswith("→")],
            "n_evidence": len(r.get("evidence") or []),
            "body_preview": (r.get("body") or "")[:160],
        })
    out = {
        "slug": slug,
        "status": row["status"],
        "updated_at": row["updated_at"],
        "draft_eq_published": (row["draft_json"] or "") == (row["published_json"] or ""),
        "kpi_relations": kpi_n,
        "n_relations": len(rels),
        "kpi_matches": str(kpi_n) == str(len(rels)),
        "labels": {},
        "relations": cards,
    }
    for r in rels:
        lb = (r.get("label") or "").strip() or "(empty)"
        out["labels"][lb] = out["labels"].get(lb, 0) + 1
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
