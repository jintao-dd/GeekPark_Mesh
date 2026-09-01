#!/usr/bin/env python3
"""Restore tmesh issue draft from prod published_json (read prod, write tmesh only)."""
from __future__ import annotations

import json
import subprocess
import sys

slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"


def _prod_published() -> str:
    cmd = [
        "docker", "exec", "geekpark-mesh", "python", "-c",
        f"import app.db as d; c=d.connect(); r=c.execute(\"SELECT published_json FROM issues WHERE slug='{slug}'\").fetchone(); print(r['published_json'] or '')",
    ]
    out = subprocess.check_output(cmd, text=True)
    return out.strip()


def _restore_tmesh(payload: str) -> None:
    import app.db as db

    con = db.connect()
    row = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row:
        raise SystemExit(f"tmesh issue missing: {slug}")
    issue_id = row["id"]
    con.execute(
        "UPDATE issues SET draft_json=?, published_json=NULL, status='draft', published_at=NULL WHERE id=?",
        (payload, issue_id),
    )
    db.reindex_issue(con, issue_id)
    con.commit()
    data = json.loads(payload)
    print(json.dumps({
        "restored": True,
        "slug": slug,
        "n_relations": len(data.get("relations") or []),
        "status": "draft",
    }, ensure_ascii=False))


if __name__ == "__main__":
    pub = _prod_published()
    if not pub:
        raise SystemExit("prod published_json empty")
    _restore_tmesh(pub)
