#!/usr/bin/env python3
"""Strip meta copy like「记录标注xx团队用得上」from relation narratives."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from app import db

# 「记录标注…用得上」「明确标注…用得上」等元叙述，不应出现在读者文案
_META_ROUTE = re.compile(
    r"[，,]?\s*(?:记录|文中|材料|纪要)?(?:明确)?(?:标注|写明|注明)[^。；;]{0,40}用得上[。．]?",
)
_META_ROUTE2 = re.compile(
    r"[，,]?\s*记录里?(?:写着|提到)[^。；;]{0,30}用得上[。．]?",
)


def clean_text(s: str) -> str:
    t = (s or "").strip()
    if not t:
        return t
    prev = None
    while prev != t:
        prev = t
        t = _META_ROUTE.sub("", t)
        t = _META_ROUTE2.sub("", t)
    t = re.sub(r"[，,]{2,}", "，", t)
    t = re.sub(r"。{2,}", "。", t)
    t = re.sub(r"\s+", " ", t).strip(" ，,")
    if t and t[-1] not in "。！？!?":
        # keep as-is; don't force period
        pass
    return t


def clean_rel(rel: dict) -> tuple[dict, bool]:
    row = dict(rel)
    changed = False
    for key in ("body", "title"):
        old = row.get(key) or ""
        new = clean_text(str(old))
        if new != old:
            row[key] = new
            changed = True
    details = row.get("details") or []
    new_details = []
    for d in details:
        nd = clean_text(str(d))
        if nd != d:
            changed = True
        if nd:
            new_details.append(nd)
    if new_details != details:
        row["details"] = new_details
        changed = True
    return row, changed


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
    changed_n = 0
    samples = []
    new_rels = []
    for r in draft.get("relations") or []:
        if not isinstance(r, dict):
            continue
        nr, ch = clean_rel(r)
        if ch:
            changed_n += 1
            samples.append({
                "before_body": (r.get("body") or "")[:120],
                "after_body": (nr.get("body") or "")[:120],
                "title": nr.get("title"),
            })
        new_rels.append(nr)
    draft["relations"] = new_rels

    # also clean reader/backlog mirrors
    for key in ("_relations_reader", "_relations_backlog"):
        arr = []
        for r in draft.get(key) or []:
            if isinstance(r, dict):
                nr, _ = clean_rel(r)
                arr.append(nr)
            else:
                arr.append(r)
        draft[key] = arr

    pub = json.loads(row["published_json"] or "{}") if row["published_json"] else dict(draft)
    pub_rels = []
    for r in pub.get("relations") or []:
        if isinstance(r, dict):
            nr, _ = clean_rel(r)
            pub_rels.append(nr)
        else:
            pub_rels.append(r)
    # keep published as reader subset if draft has reader list
    reader = draft.get("_relations_reader")
    if reader is not None:
        pub = dict(draft)
        pub["relations"] = reader
    else:
        pub["relations"] = pub_rels
        for k in ("kpis", "lead", "contacts", "keywords", "plans", "views"):
            if k in draft:
                pub[k] = draft[k]

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
            "relation_copy",
            "记录标注…用得上",
            f"stripped meta route copy from {changed_n} cards",
        ),
    )
    con.commit()
    con.close()
    print(json.dumps({
        "ok": True,
        "slug": slug,
        "n_changed": changed_n,
        "samples": samples[:8],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
