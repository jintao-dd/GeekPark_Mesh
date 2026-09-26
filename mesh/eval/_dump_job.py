#!/usr/bin/env python3
"""Dump raw job_store row for pipeline/preview."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SLUG = (sys.argv[1] if len(sys.argv) > 1 else "2026-09-08").strip()


def main() -> int:
    from app import db, job_store, pipeline, preview_job

    pst = job_store.get("pipeline", SLUG, pipeline._defaults(SLUG))
    vst = job_store.get("preview", SLUG, preview_job._defaults(SLUG))
    print("PIPELINE", json.dumps(pst, ensure_ascii=False, default=str)[:4000])
    print("PREVIEW", json.dumps(vst, ensure_ascii=False, default=str)[:1500])
    # also mesh_jobs if present
    con = db.connect()
    try:
        rows = list(
            con.execute(
                "SELECT kind, key, status, updated_at, substr(cast(payload as text),1,500) p "
                "FROM mesh_jobs WHERE key=? ORDER BY updated_at DESC LIMIT 5",
                (SLUG,),
            )
        )
        print("MESH_JOBS", [dict(r) for r in rows])
    except Exception as e:
        print("MESH_JOBS_ERR", e)
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
