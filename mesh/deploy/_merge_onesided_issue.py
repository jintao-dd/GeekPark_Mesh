#!/usr/bin/env python3
"""Merge same-entity one-sided/watch cards into one card with multiple → teams."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime

from app import db
from app.aggregator import sanitize_owner_team
from app.owner_guard import is_suggested_team_badge, normalize_relation_team_badges
from app.relation_display import attach_reader_flags, split_relations_for_publish
from app.relation_writer import strip_route_meta_copy


def _entity_key(title: str) -> str:
    t = (title or "").strip()
    if not t:
        return ""
    m = re.match(r"^([^（(：:\s·•]{2,24})", t)
    return (m.group(1).strip().lower() if m else t[:24].lower())


def _is_onesided(r: dict) -> bool:
    label = r.get("label") or ""
    tier = r.get("decision_tier") or ""
    return "一方接触" in label or tier == "watch" or bool(r.get("weak"))


def _merge_teams(a: list, b: list) -> list[str]:
    solid: list[str] = []
    sug: list[str] = []
    for t in list(a or []) + list(b or []):
        s = str(t).strip()
        if not s:
            continue
        if is_suggested_team_badge(s):
            name = sanitize_owner_team(s.lstrip("→").lstrip("->").strip()) or s.lstrip("→").lstrip("->").strip()
            badge = f"→ {name}"
            if badge not in sug:
                sug.append(badge)
        else:
            name = sanitize_owner_team(s) or s
            if name and name not in solid:
                solid.append(name)
    return solid + sug


def _merge_card(base: dict, other: dict) -> dict:
    out = dict(base)
    out["teams"] = _merge_teams(out.get("teams") or [], other.get("teams") or [])
    dets = list(out.get("details") or [])
    for d in other.get("details") or []:
        if d and d not in dets:
            dets.append(d)
    out["details"] = dets
    srcs = list(out.get("sources") or [])
    for s in other.get("sources") or []:
        if s and s not in srcs:
            srcs.append(s)
    out["sources"] = srcs
    ev = list(out.get("evidence") or [])
    seen = {e.get("item_id") for e in ev if isinstance(e, dict)}
    for e in other.get("evidence") or []:
        if isinstance(e, dict) and e.get("item_id") not in seen:
            ev.append(e)
            seen.add(e.get("item_id"))
    out["evidence"] = ev
    b1 = strip_route_meta_copy(out.get("body") or "")
    b2 = strip_route_meta_copy(other.get("body") or "")
    out["body"] = b2 if len(b2) >= len(b1) else b1
    out["title"] = strip_route_meta_copy(out.get("title") or "")
    out["body"] = strip_route_meta_copy(out.get("body") or "")
    out["details"] = [
        strip_route_meta_copy(str(d)) for d in (out.get("details") or []) if str(d).strip()
    ]
    return normalize_relation_team_badges(out, suggest_extra_solid=True)


def merge_onesided_duplicates(rels: list[dict]) -> tuple[list[dict], list[dict]]:
    buckets: dict[str, list[int]] = {}
    for i, r in enumerate(rels):
        if not _is_onesided(r):
            continue
        key = _entity_key(r.get("title") or "")
        if not key:
            continue
        buckets.setdefault(key, []).append(i)

    absorb: set[int] = set()
    log: list[dict] = []
    for key, idxs in buckets.items():
        if len(idxs) < 2:
            continue
        primary = idxs[0]
        card = dict(rels[primary])
        titles = [rels[primary].get("title")]
        for j in idxs[1:]:
            card = _merge_card(card, rels[j])
            titles.append(rels[j].get("title"))
            absorb.add(j)
        rels[primary] = card
        log.append({
            "entity": key,
            "from_n": len(idxs),
            "titles": titles,
            "teams": card.get("teams"),
            "title": card.get("title"),
            "body": card.get("body"),
        })

    out = [r for i, r in enumerate(rels) if i not in absorb]
    return out, log


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-01"
    con = db.connect()
    row = con.execute(
        "SELECT id, draft_json FROM issues WHERE slug=?", (slug,)
    ).fetchone()
    if not row:
        raise SystemExit(f"missing {slug}")
    draft = json.loads(row["draft_json"] or "{}")
    rels = [dict(r) for r in (draft.get("relations") or []) if isinstance(r, dict)]
    for r in rels:
        r["title"] = strip_route_meta_copy(r.get("title") or "")
        r["body"] = strip_route_meta_copy(r.get("body") or "")
        r["details"] = [
            strip_route_meta_copy(str(d)) for d in (r.get("details") or []) if str(d).strip()
        ]
        r["details"] = [d for d in r["details"] if d]

    before = len(rels)
    merged, log = merge_onesided_duplicates(rels)
    final = attach_reader_flags(merged)
    reader, backlog = split_relations_for_publish(final)
    draft["relations"] = final
    draft["_relations_reader"] = reader
    draft["_relations_backlog"] = backlog
    for k in draft.get("kpis") or []:
        if k.get("label") == "可同步的关系":
            k["n"] = str(len(final))
    pub = dict(draft)
    pub["relations"] = reader
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
        (int(row["id"]), "deploy-fix", "merge_onesided", f"n={before}", f"n={len(final)}; merges={len(log)}"),
    )
    with db.write_lock():
        db.commit_retry(con)
    con.close()
    print(json.dumps({
        "ok": True,
        "slug": slug,
        "before": before,
        "after": len(final),
        "kpi": next((k.get("n") for k in (draft.get("kpis") or []) if k.get("label") == "可同步的关系"), None),
        "merges": log,
        "titles": [r.get("title") for r in final],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
