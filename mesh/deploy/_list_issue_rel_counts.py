"""List recent issue slugs with relation counts (for A1.2 second-period shadow)."""
from __future__ import annotations

import json
import sys

from app import db


def main() -> None:
    con = db.connect()
    rows = con.execute(
        "SELECT id, slug, status FROM issues ORDER BY id DESC LIMIT 12"
    ).fetchall()
    out = []
    for r in rows:
        draft = con.execute(
            "SELECT draft_json, published_json FROM issues WHERE id=?", (r["id"],)
        ).fetchone()
        n_d = n_p = 0
        for key, attr in (("draft", "draft_json"), ("pub", "published_json")):
            raw = draft[attr]
            if not raw:
                continue
            data = json.loads(raw) if isinstance(raw, str) else raw
            n = len(data.get("relations") or [])
            if key == "draft":
                n_d = n
            else:
                n_p = n
        out.append(
            {
                "slug": r["slug"],
                "status": r["status"],
                "n_draft_rels": n_d,
                "n_pub_rels": n_p,
            }
        )
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
