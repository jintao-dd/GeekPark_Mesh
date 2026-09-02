#!/usr/bin/env python3
"""为何 raw candidate 很少：实体跨团队 vs provenance 拦截。"""
from __future__ import annotations
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.aggregator import sanitize_owner_team
from app.owner_guard import cross_team_provenance_ok
from app.relation_candidates import prepare_draft_bundle, _build_raw_candidates


def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-09-02"
    con = db.connect()
    issue = con.execute("SELECT id FROM issues WHERE slug=?", (slug,)).fetchone()
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    con.close()
    items = bundle["item_rows"]
    active = [dict(x) for x in items if not x.get("blocked")]

    by_entity: dict[str, set[str]] = defaultdict(set)
    for it in active:
        ot = sanitize_owner_team(it.get("owner_team"))
        if not ot or ot == "外部媒体":
            continue
        ents = it.get("entities")
        if isinstance(ents, str):
            try:
                ents = json.loads(ents)
            except Exception:
                ents = []
        for name in ents or []:
            if len(str(name).strip()) >= 2:
                by_entity[str(name).strip()].add(ot)

    multi = {e: sorted(ts) for e, ts in by_entity.items() if len(ts) >= 2}
    provenance_ok = []
    provenance_fail = []
    for e, teams in sorted(multi.items(), key=lambda x: (-len(x[1]), x[0])):
        if cross_team_provenance_ok(active, e, teams):
            provenance_ok.append({"entity": e, "teams": teams})
        else:
            provenance_fail.append({"entity": e, "teams": teams})

    raw = _build_raw_candidates(items)
    print(json.dumps({
        "slug": slug,
        "n_items": len(active),
        "n_entities_multi_team": len(multi),
        "n_provenance_ok": len(provenance_ok),
        "n_provenance_fail": len(provenance_fail),
        "n_raw_candidates": len(raw),
        "provenance_ok_sample": provenance_ok[:20],
        "provenance_fail_sample": provenance_fail[:15],
        "raw_titles": [c.get("title") for c in raw],
        "teams_in_issue": sorted({sanitize_owner_team(it.get("owner_team")) for it in active}),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
