# -*- coding: utf-8 -*-
"""Post-Preview narrative hygiene check. Usage: python _check_writer_hygiene.py <slug>"""
from __future__ import annotations

import json
import sys

from app import db
from app.relation_writer import (
    body_is_template,
    body_restates_details,
    title_has_label_leak,
    title_is_formula,
)

slug = (sys.argv[1] if len(sys.argv) > 1 else "").strip()
con = db.connect()
try:
    row = con.execute(
        "SELECT draft_json, period_label FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row:
        print(json.dumps({"error": "no_issue", "slug": slug}))
        raise SystemExit(1)
    draft = json.loads(row["draft_json"] or "{}")
    rels = list(draft.get("relations") or [])
    title_leaks = []
    title_formulas = []
    body_shells = []
    body_restates = []
    samples = []
    for i, r in enumerate(rels):
        if not isinstance(r, dict):
            continue
        title = (r.get("title") or "").strip()
        body = (r.get("body") or "").strip()
        details = [str(d).strip() for d in (r.get("details") or []) if str(d).strip()]
        if title_has_label_leak(title):
            title_leaks.append({"i": i, "title": title})
        if title_is_formula(title):
            title_formulas.append({"i": i, "title": title})
        if body_is_template(body):
            body_shells.append({"i": i, "body": body[:80]})
        if body and body_restates_details(body, details):
            body_restates.append({"i": i, "body": body[:100]})
        if len(samples) < 8:
            samples.append({
                "title": title,
                "label": r.get("label"),
                "body": body[:120],
                "details": details[:2],
                "flags": {
                    "title_rebuilt": bool(r.get("_title_rebuilt_from_details")),
                    "body_restates": bool(r.get("_body_omitted_restates_details")),
                    "body_template": bool(r.get("_body_omitted_template")),
                },
            })
    out = {
        "slug": slug,
        "period": row["period_label"],
        "n_relations": len(rels),
        "n_title_label_leak": len(title_leaks),
        "n_title_formula": len(title_formulas),
        "n_body_template": len(body_shells),
        "n_body_restates": len(body_restates),
        "title_formulas": title_formulas[:10],
        "body_restates": body_restates[:10],
        "samples": samples,
        "pass": (
            len(title_leaks) == 0
            and len(title_formulas) == 0
            and len(body_shells) == 0
            and len(body_restates) == 0
        ),
    }
    print(json.dumps(out, ensure_ascii=False, default=str))
finally:
    con.close()
