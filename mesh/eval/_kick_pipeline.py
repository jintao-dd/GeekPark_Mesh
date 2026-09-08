#!/usr/bin/env python3
"""Kick pipeline + print job state for a slug (tmesh Final Preview E2E)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SLUG = (sys.argv[1] if len(sys.argv) > 1 else "2026-09-08").strip()
FORCE = "--force" in sys.argv
REEXTRACT = "--reextract" in sys.argv


def main() -> int:
    from app import db, pipeline, job_store

    con = db.connect()
    row = con.execute(
        "SELECT id, slug, status FROM issues WHERE slug=?", (SLUG,)
    ).fetchone()
    if not row:
        print(f"FAIL: no issue {SLUG}")
        return 1
    n_src = con.execute(
        "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND length(COALESCE(text,''))>0",
        (row["id"],),
    ).fetchone()["c"]
    n_ext = con.execute(
        "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND extracted=1",
        (row["id"],),
    ).fetchone()["c"]
    n_items = con.execute(
        "SELECT COUNT(*) c FROM items WHERE issue_id=?", (row["id"],)
    ).fetchone()["c"]
    con.close()
    print(
        json.dumps(
            {
                "slug": SLUG,
                "status": row["status"],
                "sources": n_src,
                "extracted": n_ext,
                "items": n_items,
                "force": FORCE,
                "reextract": REEXTRACT,
            },
            ensure_ascii=False,
        )
    )
    pipeline.start(SLUG, force=FORCE, reextract=REEXTRACT)
    st = job_store.get("pipeline", SLUG, pipeline._defaults(SLUG))
    print(json.dumps({"pipeline": st}, ensure_ascii=False, default=str)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
