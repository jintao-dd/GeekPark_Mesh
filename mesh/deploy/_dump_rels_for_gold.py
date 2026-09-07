"""Dump relations+items for Relation Gold sampling. Run inside container."""
from __future__ import annotations

import json
import sys

from app import db


def main() -> None:
    target = (sys.argv[1:] or ["tmesh"])[0]
    con = db.connect()
    issues = con.execute(
        "SELECT id, slug, status FROM issues ORDER BY id DESC LIMIT 6"
    ).fetchall()
    out: dict = {"target": target, "issues": []}
    for iss in issues:
        issue: dict = {
            "id": iss["id"],
            "slug": iss["slug"],
            "status": iss["status"],
            "relations": [],
        }
        items = [
            dict(r)
            for r in con.execute(
                """SELECT id, source_id, owner_team, pointer, entities, blocked, text
                   FROM items WHERE issue_id=? AND COALESCE(blocked,0)=0
                   ORDER BY id LIMIT 800""",
                (iss["id"],),
            ).fetchall()
        ]
        by_id = {it["id"]: it for it in items}
        seen_titles: set[str] = set()
        for col in ("published_json", "draft_json"):
            row = con.execute(
                f"SELECT {col} AS payload FROM issues WHERE id=?", (iss["id"],)
            ).fetchone()
            raw = row["payload"] if row else None
            if not raw:
                continue
            d = json.loads(raw) if isinstance(raw, str) else raw
            for rel in d.get("relations") or []:
                if not isinstance(rel, dict):
                    continue
                title = (rel.get("title") or "").strip()
                key = f"{col}:{title}"
                if key in seen_titles:
                    continue
                seen_titles.add(key)
                ev = rel.get("evidence") or []
                item_ids = []
                for e in ev:
                    if isinstance(e, dict) and e.get("item_id") is not None:
                        item_ids.append(e["item_id"])
                linked = []
                for iid in item_ids:
                    it = by_id.get(iid)
                    if it:
                        linked.append(
                            {
                                "id": it["id"],
                                "owner_team": it.get("owner_team"),
                                "pointer": it.get("pointer"),
                                "source_id": it.get("source_id"),
                                "text": (it.get("text") or "")[:500],
                                "entities": it.get("entities"),
                            }
                        )
                issue["relations"].append(
                    {
                        "lane": col,
                        "title": title,
                        "body": rel.get("body"),
                        "details": rel.get("details"),
                        "teams": rel.get("teams"),
                        "decision_tier": rel.get("decision_tier"),
                        "relation_type": rel.get("relation_type"),
                        "label": rel.get("label"),
                        "weak": rel.get("weak"),
                        "evidence": [
                            {
                                "item_id": e.get("item_id"),
                                "team": e.get("team"),
                                "snippet": (e.get("snippet") or e.get("quote") or "")[:300],
                            }
                            for e in ev
                            if isinstance(e, dict)
                        ],
                        "items": linked,
                    }
                )
        out["issues"].append(issue)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
