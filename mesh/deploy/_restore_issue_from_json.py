#!/usr/bin/env python3
"""Restore issue JSON dump into current DB (tmesh)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app import db

path = Path(sys.argv[1])


def main() -> None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    slug = payload["issue"]["slug"]
    con = db.connect()
    old = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    if old:
        oid = int(old["id"])
        con.execute("DELETE FROM cards WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM items WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM sources WHERE issue_id=?", (oid,))
        con.execute("DELETE FROM issues WHERE id=?", (oid,))
    iss = dict(payload["issue"])
    iss.pop("id", None)
    cols = list(iss.keys())
    con.execute(
        f"INSERT INTO issues({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
        [iss[c] for c in cols],
    )
    new_id = int(con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()["id"])

    sid_map: dict[int, int] = {}
    for s in payload["sources"]:
        old_sid = s.get("id")
        row = dict(s)
        row.pop("id", None)
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
        if old_sid is not None:
            sid_map[int(old_sid)] = new_sid

    iid_map: dict[int, int] = {}
    pending_merge: list[tuple[int, int]] = []
    for it in payload["items"]:
        old_iid = it.get("id")
        row = dict(it)
        row.pop("id", None)
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
        if old_iid is not None:
            iid_map[int(old_iid)] = new_iid
        if mi is not None and old_iid is not None:
            pending_merge.append((int(old_iid), int(mi)))
    for old_iid, mi in pending_merge:
        if old_iid in iid_map and mi in iid_map:
            con.execute(
                "UPDATE items SET merged_into=? WHERE id=?",
                (iid_map[mi], iid_map[old_iid]),
            )

    for c in payload["cards"]:
        row = dict(c)
        row.pop("id", None)
        row["issue_id"] = new_id
        cols = list(row.keys())
        con.execute(
            f"INSERT INTO cards({','.join(cols)}) VALUES ({','.join(['?'] * len(cols))})",
            [row[c] for c in cols],
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
    print(json.dumps({
        "restored": True,
        "slug": slug,
        "issue_id": new_id,
        "n_items": n_items,
        "n_cards": n_cards,
        "n_sources": len(payload["sources"]),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
