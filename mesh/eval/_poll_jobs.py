#!/usr/bin/env python3
"""Poll pipeline / preview job state for a slug."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SLUG = (sys.argv[1] if len(sys.argv) > 1 else "2026-09-08").strip()


def main() -> int:
    from app import db, pipeline, preview_job, job_store

    con = db.connect()
    row = con.execute(
        "SELECT id, status, length(COALESCE(draft_json,'')) dlen, "
        "length(COALESCE(published_json,'')) plen FROM issues WHERE slug=?",
        (SLUG,),
    ).fetchone()
    if not row:
        print(f"FAIL: no issue {SLUG}")
        return 1
    iid = row["id"]
    n_src = con.execute(
        "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND length(COALESCE(text,''))>0",
        (iid,),
    ).fetchone()["c"]
    n_ext = con.execute(
        "SELECT COUNT(*) c FROM sources WHERE issue_id=? AND extracted=1",
        (iid,),
    ).fetchone()["c"]
    n_items = con.execute(
        "SELECT COUNT(*) c FROM items WHERE issue_id=?", (iid,)
    ).fetchone()["c"]
    n_cards = con.execute(
        "SELECT COUNT(*) c FROM cards WHERE issue_id=?", (iid,)
    ).fetchone()["c"]
    gate = None
    try:
        draft = con.execute(
            "SELECT draft_json FROM issues WHERE id=?", (iid,)
        ).fetchone()["draft_json"]
        if draft:
            d = json.loads(draft)
            gate = {
                "ok": bool(d.get("_preview_gate_ok")),
                "stale": bool(d.get("_preview_gate_stale")),
                "building": bool(d.get("_preview_building")),
                "n_rel": len(d.get("relations") or []),
                "n_kw_groups": len((d.get("keywords") or {}).get("groups") or []),
            }
    except Exception as e:
        gate = {"error": str(e)}
    con.close()

    pst = job_store.get("pipeline", SLUG, pipeline._defaults(SLUG))
    vst = job_store.get("preview", SLUG, preview_job._defaults(SLUG))
    out = {
        "slug": SLUG,
        "status": row["status"],
        "sources": n_src,
        "extracted": n_ext,
        "items": n_items,
        "cards": n_cards,
        "draft_len": row["dlen"],
        "pub_len": row["plen"],
        "gate": gate,
        "pipeline": {
            "running": pst.get("running"),
            "done": pst.get("done"),
            "error": pst.get("error"),
            "cur": pst.get("cur"),
            "extract": (pst.get("results") or {}).get("extract"),
            "results": pst.get("results"),
            "message": (pst.get("message") or "")[:200],
            "executor": pst.get("_executor"),
            "updated_at": pst.get("_updated_at"),
        },
        "preview": {
            "running": vst.get("running"),
            "done": vst.get("done"),
            "error": vst.get("error"),
            "phase": vst.get("phase"),
            "message": (vst.get("message") or "")[:200],
            "preview_ready": vst.get("preview_ready"),
            "executor": vst.get("_executor"),
        },
    }
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
