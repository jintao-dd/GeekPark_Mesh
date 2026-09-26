"""P1 只读：抽查 llm_skip 是否可能误杀「两侧都有记录」的候选。

Usage (in container):
  python /tmp/_aud_llm_skip_falsekill.py 2026-09-15
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter

from app import db
from app.aggregator import sanitize_owner_team

slug = (sys.argv[1] if len(sys.argv) > 1 else "").strip()


def _ents(blob) -> set[str]:
    out = set()
    if isinstance(blob, list):
        for x in blob:
            s = str(x).strip()
            if len(s) >= 2:
                out.add(s)
        return out
    try:
        for x in json.loads(blob or "[]"):
            s = str(x).strip()
            if len(s) >= 2:
                out.add(s)
    except Exception:
        pass
    return out


def _snip_ents(text: str) -> set[str]:
    # light: CJK runs 2+ and latin tokens
    t = text or ""
    out = set(re.findall(r"[A-Za-z][A-Za-z0-9\-.]{1,}", t))
    out |= set(re.findall(r"[\u4e00-\u9fff]{2,8}", t))
    return {x for x in out if x not in {"团队", "编辑部", "商业化", "进行中", "沟通中", "接触中"}}


def main() -> None:
    con = db.connect()
    try:
        iss = con.execute(
            "SELECT id, draft_json, period_label FROM issues WHERE slug=?", (slug,)
        ).fetchone()
        if not iss:
            print(json.dumps({"error": "no_issue", "slug": slug}))
            return
        draft = json.loads(iss["draft_json"] or "{}")
        audit = draft.get("_relation_decision_audit") or {}
        summary = audit.get("outcome_summary") or {}
        ledger = audit.get("candidate_ledger") or audit.get("rows") or []

        items = [
            dict(r)
            for r in con.execute(
                """
                SELECT id, owner_team, entities, text, blocked, merged_into
                FROM items WHERE issue_id=? AND COALESCE(blocked,0)=0
                  AND (merged_into IS NULL OR merged_into=0)
                """,
                (iss["id"],),
            )
        ]
        # entity -> teams that mention it
        ent_teams: dict[str, set[str]] = {}
        for it in items:
            team = sanitize_owner_team(it.get("owner_team") or "") or (it.get("owner_team") or "")
            if not team:
                continue
            for e in _ents(it.get("entities")) | _snip_ents(it.get("text") or ""):
                ent_teams.setdefault(e, set()).add(team)

        suspects = []
        skip_codes = Counter()
        for row in ledger:
            if not isinstance(row, dict):
                continue
            outcome = row.get("final_outcome") or ""
            if outcome != "skipped_llm":
                continue
            code = row.get("skip_reason_code") or "llm_skip"
            skip_codes[code] += 1
            title = (row.get("candidate_title") or row.get("title") or "").strip()
            teams = [
                sanitize_owner_team(t) or t
                for t in (row.get("teams") or [])
                if str(t).strip() and not str(t).startswith(("→", "->"))
            ]
            # shared entity appearing in >=2 live teams
            shared = []
            for e, ts in ent_teams.items():
                if len(ts) < 2:
                    continue
                if e in title or any(e in (title or "") for _ in [0]):
                    if len(ts & set(teams)) >= 2 or (not teams and len(ts) >= 2):
                        shared.append({"entity": e, "teams": sorted(ts)})
                elif teams and len(ts & set(teams)) >= 2:
                    # entity not in title but teams overlap bilateral inventory
                    if any(e in (row.get("llm_reason") or "") for _ in [0]):
                        shared.append({"entity": e, "teams": sorted(ts)})
            # simpler bilateral heuristic: title token in ent_teams with 2+ teams
            title_hits = []
            for e, ts in ent_teams.items():
                if len(e) >= 2 and e in title and len(ts) >= 2:
                    title_hits.append({"entity": e, "teams": sorted(ts)})
            if title_hits:
                suspects.append({
                    "candidate_id": row.get("candidate_id"),
                    "title": title,
                    "teams": teams,
                    "llm_reason": (row.get("llm_reason") or row.get("decision_reason") or "")[:180],
                    "bilateral_entities": title_hits[:6],
                    "review_hint": "两侧库存均有该主体——抽查是否误杀",
                })

        out = {
            "slug": slug,
            "period": iss["period_label"],
            "outcome_summary": summary,
            "n_llm_skip": int(summary.get("n_skipped_llm") or 0),
            "n_suspect_falsekill": len(suspects),
            "suspects": suspects[:25],
            "note": (
                "suspect = llm_skip 且标题主体在 ≥2 个 live team 的 items 中出现。"
                "需人工点开 reason 判断：真单边/弱关系 vs 误杀。"
            ),
        }
        print(json.dumps(out, ensure_ascii=False, default=str))
    finally:
        con.close()


if __name__ == "__main__":
    main()
