#!/usr/bin/env python3
"""Manually fix KPI + restore decision_tier from audit (no issue_verify import)."""
from __future__ import annotations

import json
import sys
from datetime import datetime
from collections import Counter

from app import db
from app.relation_display import attach_reader_flags, split_relations_for_publish


def _sync_kpis(data: dict) -> dict:
    data = dict(data)
    relations = list(data.get("relations") or [])
    contacts = list(data.get("contacts") or [])
    names = []
    for c in contacts:
        for g in c.get("groups") or []:
            for it in g.get("items") or []:
                n = (it.get("name") or "").strip()
                if n:
                    names.append(n)
    kw_n = 0
    for g in (data.get("keywords") or {}).get("groups") or []:
        for it in g.get("items") or []:
            if (it.get("name") or "").strip():
                kw_n += 1
    founder_n = 0
    for c in contacts:
        for g in c.get("groups") or []:
            for it in g.get("items") or []:
                blob = json.dumps(it, ensure_ascii=False)
                if "创始" in blob or "CEO" in blob or "创始人" in blob:
                    founder_n += 1
                    break
    # preserve existing non-relation KPIs if present
    old = {k.get("label"): k.get("n") for k in (data.get("kpis") or []) if isinstance(k, dict)}
    data["kpis"] = [
        {"n": str(len(relations)), "label": "可同步的关系"},
        {"n": old.get("接触过的人") or (f"{len(names)}+" if names else "0"), "label": "接触过的人"},
        {"n": old.get("创始人级一手对话") or str(founder_n), "label": "创始人级一手对话"},
        {"n": old.get("关注的事") or str(kw_n), "label": "关注的事"},
    ]
    return data


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
    audit = draft.get("_relation_decision_audit") or {}
    by_cid = {
        (r.get("candidate_id") or ""): r
        for r in (audit.get("candidate_ledger") or audit.get("rows") or [])
        if isinstance(r, dict)
    }

    rels = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        row_r = dict(r)
        cid = (row_r.get("candidate_id") or "").strip()
        led = by_cid.get(cid) or {}
        if not row_r.get("decision_tier") and led.get("decision_tier"):
            row_r["decision_tier"] = led["decision_tier"]
        if "gate_would_cooccur" in led:
            row_r["gate_would_cooccur"] = led.get("gate_would_cooccur")
        rels.append(row_r)

    rels = attach_reader_flags(rels)
    reader, backlog = split_relations_for_publish(rels)
    draft["relations"] = rels
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    draft = _sync_kpis(draft)

    pub = dict(draft)
    pub["relations"] = reader

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
            "kpi_relations",
            "0",
            f"{len(rels)} cards; reader={len(reader)}",
        ),
    )
    con.commit()
    con.close()

    # evidence audit
    grounded = []
    for r in rels:
        ev = r.get("evidence") or []
        ok = bool(ev) and bool((r.get("body") or "").strip()) and bool((r.get("title") or "").strip())
        grounded.append({
            "candidate_id": r.get("candidate_id"),
            "title": r.get("title"),
            "label": r.get("label"),
            "tier": r.get("decision_tier"),
            "reader_visible": r.get("reader_visible"),
            "n_evidence": len(ev),
            "evidence_teams": [e.get("team") for e in ev if isinstance(e, dict)],
            "teams": r.get("teams"),
            "grounded_ok": ok,
            "weak": r.get("weak"),
        })

    print(json.dumps({
        "ok": True,
        "slug": slug,
        "kpi_relations": next(k["n"] for k in draft["kpis"] if k["label"] == "可同步的关系"),
        "n_relations": len(rels),
        "n_reader": len(reader),
        "n_backlog": len(backlog),
        "tier_counts": dict(Counter((r.get("decision_tier") or "null") for r in rels)),
        "reader_titles": [r.get("title") for r in reader],
        "n_grounded_ok": sum(1 for g in grounded if g["grounded_ok"]),
        "n_ungrounded": sum(1 for g in grounded if not g["grounded_ok"]),
        "relations": grounded,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
