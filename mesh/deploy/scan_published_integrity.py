#!/usr/bin/env python3
"""全量扫描 published_json：真假（evidence）、部门归属、来源一致性。"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.owner_guard import _DETAIL_TEAM
try:
    from app.relation_gate import issue_publish_blockers
except ImportError:
    def issue_publish_blockers(draft_json, items):  # type: ignore
        from app.relation_gate import relation_publish_blockers
        draft = json.loads(draft_json) if isinstance(draft_json, str) else (draft_json or {})
        return relation_publish_blockers(draft, items)
def _snippet_in_text(snippet: str, text: str) -> bool:
    s = (snippet or "").strip()
    t = (text or "").strip()
    if not s or not t:
        return False
    if s in t:
        return True
    head = s[:40].strip()
    return bool(head and head in t)

# 已知误标模式
FAKE_PATTERNS = [
    (re.compile(r"硅谷\s*BD.*詹杨帆"), "硅谷BD误标詹杨帆"),
    (re.compile(r"詹杨帆.*终端.*AI.*物理世界"), "詹杨帆终端假句"),
    (re.compile(r"赛力斯.*字节.*iPhone"), "赛力斯字节假句"),
]


def _parse(raw) -> dict:
    if not raw:
        return {}
    return json.loads(raw) if isinstance(raw, str) else raw


def _load_item(con, iid):
    r = con.execute(
        "SELECT id, source_id, owner_team, team, pointer, text, source_label, blocked, merged_into "
        "FROM items WHERE id=?", (iid,),
    ).fetchone()
    return dict(r) if r else None


def _load_source(con, sid):
    r = con.execute("SELECT id, title, team FROM sources WHERE id=?", (sid,)).fetchone()
    return dict(r) if r else None


def _detail_team(line: str) -> str | None:
    m = _DETAIL_TEAM.match((line or "").strip())
    return m.group(1).strip() if m else None


def scan_relation(con, rel: dict, idx: int) -> dict:
    title = rel.get("title") or f"#{idx}"
    issues: list[dict] = []
    ev = rel.get("evidence") or []
    ev_teams = {e.get("team") for e in ev if e.get("team")}
    ev_labels = {e.get("source_label") for e in ev if e.get("source_label")}
    item_teams: set[str] = set()

    for pat, label in FAKE_PATTERNS:
        for field in ("body",):
            if pat.search(rel.get(field) or ""):
                issues.append({"kind": "fake_pattern", "field": field, "label": label})
        for i, d in enumerate(rel.get("details") or []):
            if pat.search(str(d)):
                issues.append({"kind": "fake_pattern", "field": f"details[{i}]", "label": label, "text": str(d)[:100]})

    pub_sources = set(rel.get("sources") or [])
    label_from_ev: set[str] = set()

    for e in ev:
        iid = e.get("item_id")
        item = _load_item(con, iid) if iid else None
        if not item:
            issues.append({"kind": "missing_item", "item_id": iid})
            continue
        if item.get("blocked"):
            issues.append({"kind": "blocked_item_in_evidence", "item_id": iid, "team": item.get("owner_team")})
        if item.get("merged_into"):
            issues.append({"kind": "merged_item_in_evidence", "item_id": iid})
        ot = item.get("owner_team") or item.get("team") or ""
        item_teams.add(ot)
        label_from_ev.add(item.get("source_label") or "")
        et = e.get("team") or ""
        if ot and et and ot != et:
            issues.append({"kind": "team_mismatch", "item_id": iid, "evidence_team": et, "item_owner_team": ot})
        snip = e.get("snippet") or ""
        if snip and not _snippet_in_text(snip, item.get("text") or ""):
            issues.append({"kind": "snippet_not_in_item", "item_id": iid, "snippet_head": snip[:60]})
        sid = e.get("source_id")
        if sid and item.get("source_id") and int(sid) != int(item["source_id"]):
            issues.append({"kind": "source_id_mismatch", "item_id": iid, "ev_sid": sid, "item_sid": item["source_id"]})

    for i, d in enumerate(rel.get("details") or []):
        ds = str(d)
        dt = _detail_team(ds)
        if dt:
            # detail 声称某团队记录，该团队应在 evidence 中有 item
            norm = dt.replace(" ", "")
            matched = any(
                (t or "").replace(" ", "") == norm or norm in (t or "").replace(" ", "")
                for t in item_teams | ev_teams
            )
            if not matched:
                issues.append({"kind": "detail_team_no_evidence", "detail": ds[:80], "claimed_team": dt})

    for s in pub_sources:
        if not s:
            continue
        if s not in label_from_ev and not any(s in x or x in s for x in label_from_ev if x):
            issues.append({"kind": "source_label_orphan", "published_source": s})

    rel_teams = set(rel.get("teams") or [])
    for t in rel_teams:
        if t.startswith("→") or t.startswith("->"):
            continue
        if t not in item_teams and t not in ev_teams:
            issues.append({"kind": "relation_team_no_evidence", "team": t})

    if not ev and not rel.get("weak"):
        issues.append({"kind": "no_evidence_strong"})

    return {
        "index": idx,
        "title": title,
        "weak": bool(rel.get("weak")),
        "n_evidence": len(ev),
        "teams": list(rel.get("teams") or []),
        "sources": list(pub_sources),
        "item_teams": sorted(item_teams),
        "issues": issues,
        "ok": len(issues) == 0,
    }


def scan_blocked_leak(con, issue_id: int, data: dict) -> list[dict]:
    """检查 published evidence 是否引用 blocked item。"""
    blocked_ids = {
        r["id"]
        for r in con.execute(
            "SELECT id FROM items WHERE issue_id=? AND blocked=1", (issue_id,),
        )
    }
    leaks = []
    for rel in data.get("relations") or []:
        for e in rel.get("evidence") or []:
            iid = e.get("item_id")
            if iid in blocked_ids:
                leaks.append({"title": rel.get("title"), "item_id": iid, "blocked": True})
    return leaks


def main() -> int:
    slug = sys.argv[1] if len(sys.argv) > 1 else "2026-8-17"
    con = db.connect()
    row = con.execute("SELECT id, status, published_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not row:
        print("issue not found", slug)
        return 1
    data = _parse(row["published_json"])
    issue_id = row["id"]
    rels = data.get("relations") or []

    items = [
        dict(x)
        for x in con.execute(
            """SELECT id, source_id, owner_team, pointer, entities, text, source_label, blocked
               FROM items WHERE issue_id=? AND merged_into IS NULL""",
            (issue_id,),
        )
    ]
    blockers: list[str] = []
    try:
        blockers = issue_publish_blockers(data, items)
    except Exception as e:
        blockers = [f"(blockers check skipped: {e})"]

    results = [scan_relation(con, r, i) for i, r in enumerate(rels)]
    bad = [r for r in results if not r["ok"]]
    blocked_leaks = scan_blocked_leak(con, issue_id, data)

    # blocked items 仍带硅谷BD+詹杨帆
    suspicious_items = []
    for it in items:
        if not it.get("blocked"):
            continue
        txt = it.get("text") or ""
        lbl = it.get("source_label") or ""
        if "詹杨帆" in txt or "詹杨帆" in lbl:
            suspicious_items.append({
                "id": it["id"], "blocked": 1, "owner_team": it.get("owner_team"),
                "source_label": lbl, "text_head": txt[:80],
            })

    summary = {
        "slug": slug,
        "status": row["status"],
        "n_relations": len(rels),
        "n_ok": sum(1 for r in results if r["ok"]),
        "n_issues": len(bad),
        "publish_blockers_if_republished": blockers,
        "blocked_leaks_in_published": blocked_leaks,
        "blocked_suspicious_items": suspicious_items,
        "issue_rows": bad,
    }

    out_path = ROOT / "eval" / "reports" / f"scan_{slug.replace('/', '-')}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"=== SCAN {slug} ({row['status']}) ===")
    print(f"relations: {len(rels)} ok: {summary['n_ok']} with_issues: {summary['n_issues']}")
    print(f"blocked leaks in published: {len(blocked_leaks)}")
    print(f"blocked suspicious items (not in published ev): {len(suspicious_items)}")
    if blockers:
        print(f"republish blockers ({len(blockers)}):")
        for b in blockers[:5]:
            print(" ", b)
    if bad:
        print("\n--- RELATIONS WITH ISSUES ---")
        for r in bad:
            print(f"\n[{r['index']}] {r['title']} weak={r['weak']} ev={r['n_evidence']}")
            print(f"  teams={r['teams']} item_teams={r['item_teams']}")
            for iss in r["issues"]:
                print(f"  ! {iss['kind']}: {json.dumps(iss, ensure_ascii=False)}")
    else:
        print("\nAll relations passed integrity scan.")

    if blocked_leaks:
        print("\n!!! BLOCKED ITEMS IN PUBLISHED EVIDENCE !!!")
        for x in blocked_leaks:
            print(" ", x)

    print(f"Report: {out_path}")
    con.close()
    return 1 if bad or blocked_leaks else 0


if __name__ == "__main__":
    raise SystemExit(main())
