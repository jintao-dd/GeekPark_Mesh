#!/usr/bin/env python3
"""Phase A+ 验收：draft integrity + publish blockers + candidate 覆盖（不 publish）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import build_relation_candidates, prepare_draft_bundle, _strict_title_match, _title_match
from app.relation_gate import issue_publish_blockers

# reuse scan helpers
from deploy.scan_published_integrity import scan_relation, scan_blocked_leak, _parse


def _load_issue_json(con, slug: str, *, use_draft: bool) -> tuple[dict, dict, list[dict]]:
    row = con.execute(
        "SELECT id, status, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    issue = dict(row)
    raw = issue["draft_json"] if use_draft else issue["published_json"]
    data = _parse(raw)
    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (issue["id"],),
        )
    ]
    return issue, data, items


def _candidate_coverage(candidates: list[dict], relations: list[dict]) -> dict:
    used_cand: set[int] = set()
    matched: list[str] = []
    for r in relations:
        title = r.get("title") or ""
        for i, c in enumerate(candidates):
            if i in used_cand:
                continue
            if _strict_title_match(title, c.get("title") or ""):
                used_cand.add(i)
                matched.append(c.get("title") or "")
                break
    unused = [c.get("title") or "" for i, c in enumerate(candidates) if i not in used_cand]
    llm_miss = []
    for c in candidates:
        ct = c.get("title") or ""
        if not any(_strict_title_match(ct, r.get("title") or "") for r in relations):
            llm_miss.append(ct)
    return {
        "n_candidates": len(candidates),
        "n_relations": len(relations),
        "n_candidates_matched_strict": len(matched),
        "matched_titles": matched,
        "unused_candidates": unused,
        "likely_llm_skipped": llm_miss,
    }


def _integrity_summary(con, issue_id: int, data: dict) -> dict:
    rels = data.get("relations") or []
    results = [scan_relation(con, r, i) for i, r in enumerate(rels)]
    bad = [r for r in results if not r["ok"]]
    titles = [r["title"] for r in rels]
    dup = len(titles) - len(set(titles))
    no_ev = sum(1 for r in rels if not (r.get("evidence") or []))
    team_mis = sum(
        1 for r in bad for iss in r["issues"] if iss.get("kind") in ("relation_team_no_evidence", "team_mismatch")
    )
    orphan_src = sum(1 for r in bad for iss in r["issues"] if iss.get("kind") == "source_label_orphan")
    blocked_leaks = scan_blocked_leak(con, issue_id, data)
    return {
        "n_relations": len(rels),
        "n_ok": sum(1 for r in results if r["ok"]),
        "n_with_issues": len(bad),
        "duplicate_titles": dup,
        "evidence_zero": no_ev,
        "team_mismatch_issues": team_mis,
        "orphan_source_issues": orphan_src,
        "blocked_leaks": len(blocked_leaks),
        "bad_relations": [
            {"title": r["title"], "issues": [i.get("kind") for i in r["issues"]]}
            for r in bad
        ],
    }


def run_report(slug: str, *, label: str, use_draft: bool = True) -> dict:
    con = db.connect()
    issue, data, items = _load_issue_json(con, slug, use_draft=use_draft)
    bundle = prepare_draft_bundle(con, issue["id"], slug)
    candidates = bundle["relation_candidates"]
    blockers = issue_publish_blockers(data, items)
    integrity = _integrity_summary(con, issue["id"], data)
    coverage = _candidate_coverage(candidates, data.get("relations") or [])
    con.close()
    report = {
        "label": label,
        "slug": slug,
        "status": issue["status"],
        "source": "draft_json" if use_draft else "published_json",
        "integrity": integrity,
        "publish_blockers": blockers,
        "coverage": coverage,
    }
    return report


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    before_path = ROOT / "eval" / "reports" / f"phase_a_plus_before_{slug.replace('/', '-')}.json"
    after_path = ROOT / "eval" / "reports" / f"phase_a_plus_after_{slug.replace('/', '-')}.json"

    mode = sys.argv[2] if len(sys.argv) > 2 else "after"
    if mode == "before":
        rep = run_report(slug, label="before")
        before_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        print(f"Wrote {before_path}")
        return 0

    rep = run_report(slug, label="after")
    after_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    before = json.loads(before_path.read_text(encoding="utf-8")) if before_path.is_file() else None

    print("=== PHASE A+ ACCEPTANCE ===")
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    if before:
        print("\n=== DIFF (before → after) ===")
        b, a = before["integrity"], rep["integrity"]
        print(
            json.dumps(
                {
                    "relations": [b["n_relations"], a["n_relations"]],
                    "integrity_ok": [b["n_ok"], a["n_ok"]],
                    "with_issues": [b["n_with_issues"], a["n_with_issues"]],
                    "evidence_zero": [b["evidence_zero"], a["evidence_zero"]],
                    "team_mismatch": [b["team_mismatch_issues"], a["team_mismatch_issues"]],
                    "orphan_source": [b["orphan_source_issues"], a["orphan_source_issues"]],
                    "duplicate_titles": [b["duplicate_titles"], a["duplicate_titles"]],
                    "blocked_leaks": [b["blocked_leaks"], a["blocked_leaks"]],
                    "candidates": [
                        before["coverage"]["n_candidates"],
                        rep["coverage"]["n_candidates"],
                    ],
                    "candidates_matched": [
                        before["coverage"]["n_candidates_matched_strict"],
                        rep["coverage"]["n_candidates_matched_strict"],
                    ],
                },
                ensure_ascii=False,
            )
        )
    print(f"\nWrote {after_path}")
    i = rep["integrity"]
    hard_fail = (
        i["evidence_zero"] > 0
        or i["duplicate_titles"] > 0
        or i["blocked_leaks"] > 0
        or i["n_with_issues"] > 0
    )
    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
