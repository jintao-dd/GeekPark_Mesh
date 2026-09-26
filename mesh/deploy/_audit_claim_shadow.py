"""A1：对真实一期 Relation 跑 Claim Check shadow 对账（不 enforce、不改稿）。

用法（容器内）：
  PYTHONPATH=/srv/mesh python deploy/_audit_claim_shadow.py 2026-8-17
  PYTHONPATH=/srv/mesh python deploy/_audit_claim_shadow.py 2026-8-17 published
"""
from __future__ import annotations

import json
import sys

from app import db
from app.relation_claim_check import MODE_SHADOW, apply_claim_checks


def _load_issue(slug: str, lane_pref: str | None = None) -> tuple[dict, list[dict], str]:
    con = db.connect()
    row = con.execute(
        "SELECT id, slug, status, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    items = [
        dict(r)
        for r in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, blocked, text
               FROM items WHERE issue_id=?""",
            (row["id"],),
        ).fetchall()
    ]

    def _parse(raw):
        if not raw or not str(raw).strip() or str(raw).strip() in ("{}", "null"):
            return {}
        return json.loads(raw) if isinstance(raw, str) else dict(raw)

    if lane_pref == "published":
        data, lane = _parse(row["published_json"]), "published_json"
    elif lane_pref == "draft":
        data, lane = _parse(row["draft_json"]), "draft_json"
    else:
        data = _parse(row["draft_json"])
        lane = "draft_json"
        if not (data.get("relations") or []):
            data = _parse(row["published_json"])
            lane = "published_json"
    return data, items, lane


def main() -> None:
    slug = (sys.argv[1:] or ["2026-8-17"])[0]
    lane_pref = sys.argv[2] if len(sys.argv) > 2 else None
    data, items, lane = _load_issue(slug, lane_pref)
    rels = [r for r in (data.get("relations") or []) if isinstance(r, dict)]
    kept, audit = apply_claim_checks(rels, items, mode=MODE_SHADOW)

    invalid_rows = [r for r in audit.get("rows") or [] if r.get("claim_verdict") == "invalid"]
    uncertain_rows = [r for r in audit.get("rows") or [] if r.get("claim_verdict") == "uncertain"]
    valid_watchish = []
    for rel, row in zip(kept, audit.get("rows") or []):
        if row.get("claim_verdict") != "valid":
            continue
        tip = (rel.get("decision_tier") or ""), (rel.get("relation_type") or "")
        if tip[0] == "watch" or tip[1] in ("parallel_tracks", "info_complement", "one_sided"):
            valid_watchish.append(
                {
                    "title": row.get("title"),
                    "tier": tip[0],
                    "type": tip[1],
                    "claim_strength": row.get("claim_strength"),
                    "evidence_strength": row.get("evidence_strength"),
                }
            )

    from app.relation_verify import line_grounded

    lexical_pass_invalid = []
    invalid_details = []
    for rel in kept:
        cc = rel.get("_claim_check") or {}
        if cc.get("claim_verdict") != "invalid":
            continue
        raw = rel.get("_writer_raw") or {}
        body = raw.get("body") or rel.get("body") or ""
        probe = dict(rel)
        probe["title"] = raw.get("title") or rel.get("title")
        probe["body"] = body
        probe["details"] = raw.get("details") or rel.get("details")
        lg = bool(body and line_grounded(body, probe))
        detail = {
            "title": (rel.get("title") or "")[:160],
            "body": (body or "")[:240],
            "details": (raw.get("details") or rel.get("details") or [])[:3],
            "tier": rel.get("decision_tier"),
            "type": rel.get("relation_type"),
            "weak": rel.get("weak"),
            "evidence": [
                {
                    "team": e.get("team"),
                    "snippet": (e.get("snippet") or e.get("quote") or "")[:160],
                }
                for e in (rel.get("evidence") or [])[:3]
                if isinstance(e, dict)
            ],
            "reason_code": cc.get("claim_reason_code"),
            "reason": cc.get("claim_reason"),
            "claim_strength": cc.get("claim_strength"),
            "evidence_strength": cc.get("evidence_strength"),
            "lexical_body_pass": lg,
        }
        invalid_details.append(detail)
        if lg:
            lexical_pass_invalid.append(
                {
                    "title": detail["title"],
                    "reason_code": detail["reason_code"],
                    "reason": detail["reason"],
                    "claim_strength": detail["claim_strength"],
                    "evidence_strength": detail["evidence_strength"],
                }
            )

    out = {
        "slug": slug,
        "lane": lane,
        "mode": MODE_SHADOW,
        "n_relations": len(rels),
        "n_valid": audit.get("n_valid"),
        "n_invalid": audit.get("n_invalid"),
        "n_uncertain": audit.get("n_uncertain"),
        "invalid_ratio": round((audit.get("n_invalid") or 0) / max(len(rels), 1), 3),
        "by_reason": audit.get("by_reason"),
        "invalid_rows": invalid_rows,
        "invalid_details": invalid_details,
        "uncertain_rows": uncertain_rows,
        "valid_watch_parallel_sample": valid_watchish[:20],
        "lexical_pass_but_claim_invalid": lexical_pass_invalid,
        "go_nogo_hints": {
            "invalid_ratio_warn_if_gt": 0.25,
            "note": "A1 shadow only; do not enforce until human review of invalid_details",
        },
    }
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
