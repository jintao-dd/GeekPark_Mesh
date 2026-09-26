#!/usr/bin/env python3
"""Dump one issue (issue/sources/items/cards) to a JSON path inside the container."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app import db

slug = sys.argv[1]
out = Path(sys.argv[2] if len(sys.argv) > 2 else f"/tmp/prod_issue_{slug}.json")


def main() -> None:
    con = db.connect()
    issue = con.execute("SELECT * FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        raise SystemExit(f"issue missing: {slug}")
    issue = dict(issue)
    iid = int(issue["id"])
    sources = [dict(x) for x in con.execute("SELECT * FROM sources WHERE issue_id=?", (iid,))]
    items = [dict(x) for x in con.execute("SELECT * FROM items WHERE issue_id=?", (iid,))]
    cards = [dict(x) for x in con.execute("SELECT * FROM cards WHERE issue_id=?", (iid,))]
    con.close()
    for row in sources + items + cards + [issue]:
        for k, v in list(row.items()):
            if hasattr(v, "isoformat"):
                row[k] = v.isoformat()
    out.write_text(
        json.dumps(
            {"issue": issue, "sources": sources, "items": items, "cards": cards},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    print(json.dumps({
        "dumped": True,
        "slug": slug,
        "path": str(out),
        "n_sources": len(sources),
        "n_items": len(items),
        "n_cards": len(cards),
        "dlen": len(issue.get("draft_json") or ""),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
