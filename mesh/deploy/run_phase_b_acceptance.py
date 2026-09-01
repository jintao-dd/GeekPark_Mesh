#!/usr/bin/env python3
"""Phase B 验收：preview→published 同步、KPI、weak/needs_review、integrity。"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db
from app.relation_candidates import build_relation_candidates, _strict_title_match
from app.relation_gate import issue_publish_blockers

from deploy.scan_published_integrity import scan_relation, scan_blocked_leak, _parse


def _load(con, slug: str, *, published: bool) -> tuple[dict, dict, list[dict]]:
    row = con.execute(
        "SELECT id, status, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        raise SystemExit(f"issue not found: {slug}")
    issue = dict(row)
    raw = issue["published_json"] if published else issue["draft_json"]
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


def _kpi_relations(data: dict) -> tuple[int | None, int]:
    kpis = data.get("kpis") or []
    n_cards = len(data.get("relations") or [])
    kpi_n: int | None = None
    if isinstance(kpis, dict):
        kpi_val = kpis.get("relations") or kpis.get("可同步的关系")
        try:
            kpi_n = int(kpi_val) if kpi_val is not None else None
        except (TypeError, ValueError):
            kpi_n = None
    elif isinstance(kpis, list):
        for row in kpis:
            if not isinstance(row, dict):
                continue
            if (row.get("label") or "").strip() == "可同步的关系":
                raw = str(row.get("n") or "").strip().rstrip("+")
                try:
                    kpi_n = int(raw)
                except ValueError:
                    kpi_n = None
                break
    return kpi_n, n_cards


def _integrity(con, issue_id: int, data: dict) -> dict:
    rels = data.get("relations") or []
    results = [scan_relation(con, r, i) for i, r in enumerate(rels)]
    bad = [r for r in results if not r["ok"]]
    strong = [r for r in rels if isinstance(r, dict) and not r.get("weak")]
    no_ev_strong = sum(1 for r in strong if not (r.get("evidence") or []))
    team_mis = sum(
        1 for r in bad for iss in r["issues"]
        if iss.get("kind") in ("relation_team_no_evidence", "team_mismatch")
    )
    orphan = sum(1 for r in bad for iss in r["issues"] if iss.get("kind") == "source_label_orphan")
    dup = len([r.get("title") for r in rels]) - len({(r.get("title") or "").strip() for r in rels})
    return {
        "n_relations": len(rels),
        "n_weak": sum(1 for r in rels if r.get("weak")),
        "n_needs_review": sum(1 for r in rels if r.get("needs_review")),
        "n_strong": len(strong),
        "strong_no_evidence": no_ev_strong,
        "team_mismatch": team_mis,
        "orphan_sources": orphan,
        "duplicate_titles": dup,
        "blocked_leaks": len(scan_blocked_leak(con, issue_id, data)),
        "bad_cards": len(bad),
    }


def _relation_samples(rels: list[dict], n: int = 8) -> list[dict]:
    out = []
    for r in rels[:n]:
        if not isinstance(r, dict):
            continue
        out.append({
            "title": r.get("title"),
            "label": r.get("label"),
            "weak": bool(r.get("weak")),
            "needs_review": bool(r.get("needs_review")),
            "teams": r.get("teams"),
            "n_evidence": len(r.get("evidence") or []),
            "body_preview": (r.get("body") or "")[:120],
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    slug = args.slug

    con = db.connect()
    try:
        issue, pub, items = _load(con, slug, published=True)
        _, draft, _ = _load(con, slug, published=False)
        issue_id = issue["id"]

        pub_kpi, pub_n = _kpi_relations(pub)
        draft_kpi, draft_n = _kpi_relations(draft)
        draft_pub_same = (issue.get("draft_json") or "") == (issue.get("published_json") or "")

        cands = build_relation_candidates(items)
        rels = pub.get("relations") or []
        llm_miss = [
            c.get("title") or ""
            for c in cands
            if not any(_strict_title_match(c.get("title") or "", r.get("title") or "") for r in rels)
        ]

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "phase": "B",
            "slug": slug,
            "issue_id": issue_id,
            "status": issue.get("status"),
            "draft_published_synced": draft_pub_same,
            "published": {
                "kpi_relations": pub_kpi,
                "card_count": pub_n,
                "kpi_matches_cards": pub_kpi == pub_n if pub_kpi is not None else None,
                "integrity": _integrity(con, issue_id, pub),
                "samples": _relation_samples(rels),
            },
            "draft": {
                "kpi_relations": draft_kpi,
                "card_count": draft_n,
                "kpi_matches_cards": draft_kpi == draft_n if draft_kpi is not None else None,
            },
            "publish_blockers": issue_publish_blockers(pub, items),
            "n_candidates": len(cands),
            "likely_llm_skipped_candidates": llm_miss[:15],
        }

        labels = {}
        for r in rels:
            lb = (r.get("label") or "").strip() or "(empty)"
            labels[lb] = labels.get(lb, 0) + 1
        report["label_distribution"] = labels

        out_path = Path(args.out) if args.out else ROOT / "eval" / "reports" / f"phase_b_{slug.replace('/', '-')}.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

        md_path = out_path.with_suffix(".md")
        p = report["published"]
        integ = p["integrity"]
        lines = [
            f"# Phase B 验收报告 · `{slug}`",
            "",
            f"- 生成时间：{report['generated_at']}",
            f"- draft=published 同步：**{'是' if draft_pub_same else '否'}**",
            "",
            "## 读者页（published_json）",
            f"- 关系卡：**{p['card_count']}**（虚线 {integ['n_weak']} · 待核对 {integ['n_needs_review']} · 实线 {integ['n_strong']}）",
            f"- KPI 可同步的关系：**{p['kpi_relations']}**（与卡数一致：{'✓' if p['kpi_matches_cards'] else '✗'}）",
            "",
            "## Integrity（强关系）",
            f"- strong 无 evidence：{integ['strong_no_evidence']}",
            f"- team mismatch：{integ['team_mismatch']}",
            f"- orphan sources：{integ['orphan_sources']}",
            f"- duplicate titles：{integ['duplicate_titles']}",
            f"- blocked leaks：{integ['blocked_leaks']}",
            "",
            "## 发布闸门",
        ]
        blockers = report["publish_blockers"]
        if blockers:
            lines.extend(f"- {e}" for e in blockers[:12])
        else:
            lines.append("- （无关系类硬拦截；needs_review 仍须 owner 确认）")
        lines.extend(["", "## Label 分布", ""])
        for lb, cnt in sorted(labels.items(), key=lambda x: -x[1]):
            lines.append(f"- {lb}: {cnt}")
        lines.extend(["", "## 样例卡片", ""])
        for s in p["samples"]:
            flag = "虚线" if s["weak"] else "实线"
            if s["needs_review"]:
                flag += "+待核对"
            lines.append(f"- **{s['title']}** [{flag}] · {s['label']} · ev={s['n_evidence']}")
        md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        print(json.dumps({"ok": True, "json": str(out_path), "md": str(md_path), "report": report}, ensure_ascii=False, indent=2))
    finally:
        con.close()


if __name__ == "__main__":
    main()
