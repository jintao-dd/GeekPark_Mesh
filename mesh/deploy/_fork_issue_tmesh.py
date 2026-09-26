#!/usr/bin/env python3
"""Fork tmesh issue 2026-8-17 into a fresh 2026-9-30 draft for preview."""
from __future__ import annotations

import json
import sys

from app import db

SRC = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
DST = sys.argv[2] if len(sys.argv) > 2 else "2026-9-30"
PERIOD = sys.argv[3] if len(sys.argv) > 3 else "2026.9.30"


def main() -> None:
    con = db.connect()
    src = con.execute("SELECT * FROM issues WHERE slug=?", (SRC,)).fetchone()
    if not src:
        raise SystemExit(f"source missing: {SRC}")
    src = dict(src)
    src_id = int(src["id"])

    old = con.execute("SELECT id FROM issues WHERE slug=?", (DST,)).fetchone()
    if old:
        oid = int(old["id"])
        con.execute("DELETE FROM cards WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM items WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM sources WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM issues WHERE id=?", (oid,))

    iss = {
        "slug": DST,
        "period_label": PERIOD,
        "status": "draft",
        "draft_json": "{}",
        "published_json": None,
        "published_at": None,
    }
    cols = list(iss.keys())
    con.execute(
        f"INSERT INTO issues({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
        [iss[c] for c in cols],
    )
    new_id = int(con.execute("SELECT id FROM issues WHERE slug=?", (DST,)).fetchone()["id"])

    sid_map: dict[int, int] = {}
    for s in con.execute("SELECT * FROM sources WHERE issue_id=?", (src_id,)):
        row = dict(s)
        old_sid = row.pop("id")
        row["issue_id"] = new_id
        cols = list(row.keys())
        con.execute(
            f"INSERT INTO sources({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [row[c] for c in cols],
        )
        new_sid = int(
            con.execute(
                "SELECT id FROM sources WHERE issue_id=? ORDER BY id DESC LIMIT 1",
                (new_id,),
            ).fetchone()["id"]
        )
        sid_map[int(old_sid)] = new_sid

    iid_map: dict[int, int] = {}
    pending: list[tuple[int, int]] = []
    for it in con.execute("SELECT * FROM items WHERE issue_id=?", (src_id,)):
        row = dict(it)
        old_iid = row.pop("id")
        row["issue_id"] = new_id
        if row.get("source_id") is not None:
            row["source_id"] = sid_map.get(int(row["source_id"]), row["source_id"])
        mi = row.pop("merged_into", None)
        cols = list(row.keys())
        con.execute(
            f"INSERT INTO items({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [row[c] for c in cols],
        )
        new_iid = int(
            con.execute(
                "SELECT id FROM items WHERE issue_id=? ORDER BY id DESC LIMIT 1",
                (new_id,),
            ).fetchone()["id"]
        )
        iid_map[int(old_iid)] = new_iid
        if mi is not None:
            pending.append((int(old_iid), int(mi)))
    for old_iid, mi in pending:
        if old_iid in iid_map and mi in iid_map:
            con.execute(
                "UPDATE items SET merged_into=? WHERE id=?",
                (iid_map[mi], iid_map[old_iid]),
            )

    for c in con.execute("SELECT * FROM cards WHERE issue_id=?", (src_id,)):
        row = dict(c)
        row.pop("id")
        row["issue_id"] = new_id
        cols = list(row.keys())
        con.execute(
            f"INSERT INTO cards({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [row[c] for c in cols],
        )

    # Also reset 2026-8-17 to draft-ready if user prefers that slug
    con.execute(
        "UPDATE issues SET status='draft', draft_json='{}', published_json=NULL, published_at=NULL WHERE slug=?",
        (SRC,),
    )

    db.reindex_issue(con, new_id)
    con.commit()
    n_items = con.execute(
        "SELECT count(*) AS c FROM items WHERE issue_id=? AND blocked=0", (new_id,)
    ).fetchone()["c"]
    n_cards = con.execute(
        "SELECT count(*) AS c FROM cards WHERE issue_id=? AND status='approved'",
        (new_id,),
    ).fetchone()["c"]
    n_src = con.execute(
        "SELECT count(*) AS c FROM sources WHERE issue_id=?", (new_id,)
    ).fetchone()["c"]
    print(json.dumps({
        "ok": True,
        "from_slug": SRC,
        "slug": DST,
        "period_label": PERIOD,
        "status": "draft",
        "issue_id": new_id,
        "n_sources": n_src,
        "n_items": n_items,
        "n_approved_cards": n_cards,
        "also_reset_src_draft": SRC,
    }, ensure_ascii=False, indent=2))
    con.close()


if __name__ == "__main__":
    main()
