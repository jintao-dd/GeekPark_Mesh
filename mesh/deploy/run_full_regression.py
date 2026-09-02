#!/usr/bin/env python3
"""全量回归：pytest + Ask 25 + Relation integrity + 新字段验收。

用法（mesh/ 目录）：
  python deploy/run_full_regression.py
  python deploy/run_full_regression.py --skip-pytest
  python deploy/run_full_regression.py --slug 2026-8-17 --corpus golden
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "eval" / "reports"


def _run(cmd: list[str], *, cwd: Path | None = None) -> tuple[int, str]:
    p = subprocess.run(
        cmd,
        cwd=cwd or ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (p.stdout or "") + (p.stderr or "")
    return p.returncode, out


def _seed_golden(con, slug: str = "2026-8-17") -> dict:
    from eval.run_acceptance import ISSUE_EXPORT, _seed_golden_db

    return _seed_golden_db(con)


def _relation_integrity(con, slug: str, *, published: bool) -> dict:
    from deploy.scan_published_integrity import scan_blocked_leak, scan_relation, _parse

    row = con.execute(
        "SELECT id, status, draft_json, published_json FROM issues WHERE slug=?",
        (slug,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": f"issue not found: {slug}"}
    raw = row["published_json"] if published else row["draft_json"]
    data = _parse(raw)
    rels = data.get("relations") or []
    results = [scan_relation(con, r, i) for i, r in enumerate(rels)]
    bad = [r for r in results if not r["ok"]]
    strong = [r for r in rels if isinstance(r, dict) and not r.get("weak")]
    strong_no_ev = sum(1 for r in strong if not (r.get("evidence") or []))
    team_mis = sum(
        1 for r in bad for iss in r["issues"]
        if iss.get("kind") in ("relation_team_no_evidence", "team_mismatch")
    )
    orphan = sum(1 for r in bad for iss in r["issues"] if iss.get("kind") == "source_label_orphan")
    blocked = scan_blocked_leak(con, row["id"], data)
    return {
        "ok": not bad and not blocked,
        "n_relations": len(rels),
        "n_ok": len(rels) - len(bad),
        "strong_no_evidence": strong_no_ev,
        "team_mismatch": team_mis,
        "orphan_sources": orphan,
        "blocked_leaks": len(blocked),
        "bad_titles": [r["title"] for r in bad[:8]],
    }


def _new_field_checks_from_data(data: dict) -> dict:
    from app.relation_display import reader_visible

    rels = [r for r in (data.get("relations") or []) if isinstance(r, dict)]
    backlog = data.get("_relations_backlog") or [r for r in rels if not reader_visible(r)]
    reader = [r for r in rels if reader_visible(r)]
    tiers = Counter((r.get("decision_tier") or "null") for r in rels)
    missing_tier = [r.get("title") for r in rels if not r.get("decision_tier")]
    audit = data.get("_relation_decision_audit") or {}
    return {
        "ok": len(missing_tier) == 0,
        "n_draft_relations": len(rels),
        "n_reader_visible": len(reader),
        "n_draft_backlog": len(backlog),
        "decision_tier_counts": dict(tiers),
        "missing_decision_tier": missing_tier[:10],
        "audit_summary": audit.get("outcome_summary"),
    }


def _new_field_checks(con, slug: str) -> dict:
    from app.relation_display import reader_visible

    row = con.execute(
        """SELECT id, status, draft_json, published_json,
                  embedding_status, embedding_total, embedding_done, embedding_model
           FROM issues WHERE slug=?""",
        (slug,),
    ).fetchone()
    if not row:
        return {"ok": False, "error": "issue missing"}
    draft = json.loads(row["draft_json"] or "{}")
    rels = [r for r in (draft.get("relations") or []) if isinstance(r, dict)]
    backlog = draft.get("_relations_backlog") or [
        r for r in rels if not reader_visible(r)
    ]
    reader = [r for r in rels if reader_visible(r)]
    tiers = Counter((r.get("decision_tier") or "null") for r in rels)
    missing_tier = [r.get("title") for r in rels if not r.get("decision_tier")]
    audit = draft.get("_relation_decision_audit") or {}
    return {
        "ok": len(missing_tier) == 0,
        "n_draft_relations": len(rels),
        "n_reader_visible": len(reader),
        "n_draft_backlog": len(backlog),
        "decision_tier_counts": dict(tiers),
        "missing_decision_tier": missing_tier[:10],
        "audit_summary": audit.get("outcome_summary"),
        "embedding_status": row["embedding_status"] or "",
        "embedding_progress": f"{row['embedding_done'] or 0}/{row['embedding_total'] or 0}",
        "embedding_model": row["embedding_model"] or "",
        "issue_status": row["status"],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--corpus", choices=("golden", "prod"), default="golden")
    ap.add_argument("--skip-pytest", action="store_true")
    ap.add_argument("--skip-ask", action="store_true")
    args = ap.parse_args()

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "slug": args.slug,
        "corpus": args.corpus,
        "checks": {},
        "pass": True,
        "failures": [],
    }

    # --- pytest ---
    if not args.skip_pytest:
        code, out = _run([sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"])
        tail = "\n".join(out.strip().splitlines()[-5:])
        failed = 0
        for line in out.splitlines():
            if " failed" in line and "passed" in line:
                try:
                    failed = int(line.split(" failed")[0].rsplit(", ", 1)[-1])
                except ValueError:
                    pass
        report["checks"]["pytest"] = {
            "ok": code == 0,
            "exit_code": code,
            "tail": tail,
        }
        if code != 0:
            report["pass"] = False
            report["failures"].append(f"pytest: {tail}")

    # --- Ask 25 ---
    if not args.skip_ask:
        code, out = _run([
            sys.executable, "eval/run_final_eval.py", "--corpus", args.corpus,
        ])
        ask_pass = "25/25 pass" in out
        summary = ""
        for line in out.splitlines():
            if "检索:" in line or "25/" in line:
                summary = line.strip()
        report["checks"]["ask_25"] = {
            "ok": code == 0 and ask_pass,
            "exit_code": code,
            "summary": summary or out.strip().splitlines()[-1] if out.strip() else "",
        }
        if not report["checks"]["ask_25"]["ok"]:
            report["pass"] = False
            report["failures"].append(f"ask_25: {summary}")

    # --- DB-backed checks (golden seed or prod) ---
    from app import db, db_conn

    restore = None
    if args.corpus == "prod":
        if not (db_conn.MESH_DB_URL or "").startswith("postgres"):
            report["checks"]["db_checks"] = {"ok": False, "error": "prod needs MESH_DB_URL"}
            report["pass"] = False
            report["failures"].append("db_checks: no prod DB")
            con = None
        else:
            con = db.connect()
    else:
        td = tempfile.mkdtemp(prefix="mesh_regress_")
        db_path = Path(td) / "regress.db"
        old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
        db_conn.DB_PATH = str(db_path)
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = str(db_path)
        restore = (old_path, old_url)
        con = db.connect()
        db.init_db(seed=True)
        _seed_golden(con)

    if con is not None:
        try:
            integ_pub = _relation_integrity(con, args.slug, published=True)
            row = con.execute(
                "SELECT draft_json, published_json FROM issues WHERE slug=?", (args.slug,),
            ).fetchone()
            has_draft = bool((row["draft_json"] or "").strip()) if row else False
            integ_draft = (
                _relation_integrity(con, args.slug, published=False)
                if has_draft
                else integ_pub
            )
            fields = _new_field_checks(con, args.slug)
            if row and not has_draft and (row["published_json"] or "").strip():
                fields = _new_field_checks_from_data(json.loads(row["published_json"]))
                fields["issue_status"] = con.execute(
                    "SELECT status FROM issues WHERE slug=?", (args.slug,),
                ).fetchone()["status"]
                emb = con.execute(
                    """SELECT embedding_status, embedding_total, embedding_done, embedding_model
                       FROM issues WHERE slug=?""",
                    (args.slug,),
                ).fetchone()
                if emb:
                    fields["embedding_status"] = emb["embedding_status"] or ""
                    fields["embedding_progress"] = f"{emb['embedding_done'] or 0}/{emb['embedding_total'] or 0}"
                    fields["embedding_model"] = emb["embedding_model"] or ""
            rel_tests_code, rel_out = _run([
                sys.executable, "-m", "pytest",
                "tests/test_relation_decision_v2.py",
                "tests/test_relation_candidates.py",
                "tests/test_evidence_loop.py",
                "tests/test_embedding_status.py",
                "tests/test_relation_display.py",
                "tests/test_relation_decision_coverage.py",
                "tests/test_relation_two_phase.py",
                "tests/test_relation_writer.py",
                "tests/test_relation_label_gate.py",
                "-q", "--tb=no",
            ])
            rel_tail = "\n".join(rel_out.strip().splitlines()[-3:])
            report["checks"]["relation_pytest"] = {
                "ok": rel_tests_code == 0,
                "tail": rel_tail,
            }
            report["checks"]["relation_integrity_published"] = integ_pub
            report["checks"]["relation_integrity_draft"] = integ_draft
            report["checks"]["new_fields"] = fields

            rel_ok = (
                integ_pub.get("ok")
                and integ_pub.get("strong_no_evidence", 1) == 0
                and integ_pub.get("team_mismatch", 1) == 0
                and integ_pub.get("blocked_leaks", 1) == 0
                and integ_draft.get("strong_no_evidence", 1) == 0
                and integ_draft.get("team_mismatch", 1) == 0
                and integ_draft.get("blocked_leaks", 1) == 0
                and rel_tests_code == 0
            )
            report["checks"]["relation_25"] = {
                "ok": rel_ok,
                "published_relations": integ_pub.get("n_relations"),
                "published_ok": integ_pub.get("n_ok"),
                "evidence_100": integ_pub.get("strong_no_evidence", 0) == 0,
                "team_mismatch": integ_pub.get("team_mismatch", 0),
                "blocked_leakage": integ_pub.get("blocked_leaks", 0),
            }
            if not rel_ok:
                report["pass"] = False
                report["failures"].append(
                    f"relation: strong_no_ev={integ_draft.get('strong_no_evidence')} "
                    f"team_mis={integ_draft.get('team_mismatch')} "
                    f"blocked={integ_draft.get('blocked_leaks')}"
                )
            if not fields.get("ok"):
                report["pass"] = False
                report["failures"].append(
                    f"new_fields: missing decision_tier on {fields.get('missing_decision_tier')}"
                )
        finally:
            con.close()
            if restore:
                db_conn.DB_PATH, db_conn.MESH_DB_URL = restore

    out_path = REPORT_DIR / f"full_regression_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Full Regression Report",
        "",
        f"- Time: {report['generated_at']}",
        f"- Overall: **{'PASS' if report['pass'] else 'FAIL'}**",
        f"- Report: `{out_path.name}`",
        "",
        "## Checks",
        "",
    ]
    for name, chk in report.get("checks", {}).items():
        ok = chk.get("ok", False)
        md_lines.append(f"- **{name}**: {'PASS' if ok else 'FAIL'} — `{json.dumps(chk, ensure_ascii=False)[:200]}`")
    if report.get("failures"):
        md_lines.extend(["", "## Failures", ""])
        md_lines.extend(f"- {f}" for f in report["failures"])
    md_path = REPORT_DIR / "FULL_REGRESSION_LATEST.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nReport: {out_path}")
    print(f"Summary: {md_path}")
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
