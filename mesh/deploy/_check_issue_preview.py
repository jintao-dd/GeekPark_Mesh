#!/usr/bin/env python3
"""Check issue + preview job status (prod or tmesh)."""
from __future__ import annotations

import json
import sys

from app import db, job_store

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"


def main() -> None:
    con = db.connect()
    r = con.execute(
        "SELECT id, slug, period_label, status, length(draft_json) AS dlen, "
        "length(published_json) AS plen, updated_at, published_at "
        "FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not r:
        print(json.dumps({"missing": True, "slug": slug}))
        return
    d = dict(r)
    n_items = con.execute(
        "SELECT count(*) AS c FROM items WHERE issue_id=? AND blocked=0",
        (d["id"],),
    ).fetchone()["c"]
    n_cards = con.execute(
        "SELECT count(*) AS c FROM cards WHERE issue_id=? AND status='approved'",
        (d["id"],),
    ).fetchone()["c"]
    raw = con.execute("SELECT draft_json FROM issues WHERE id=?", (d["id"],)).fetchone()["draft_json"]
    draft = json.loads(raw or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    con.close()
    st = job_store.get("preview", slug, {}) or {}
    out = {
        "slug": slug,
        "issue": {k: (str(v) if str(k).endswith("_at") else v) for k, v in d.items()},
        "n_items": n_items,
        "n_approved_cards": n_cards,
        "n_draft_relations": len(draft.get("relations") or []),
        "n_reader": len(draft.get("_relations_reader") or []),
        "audit_summary": audit.get("outcome_summary")
        or {
            k: audit.get(k)
            for k in ("n_candidates", "n_draft_relations", "n_reader_relations", "n_llm_decisions")
            if audit.get(k) is not None
        },
        "preview_job": {
            k: st.get(k) for k in ("running", "done", "error", "message")
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
