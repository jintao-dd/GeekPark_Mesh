#!/usr/bin/env python3
"""tmesh full Preview + relation audit dump (no Publish, no Prod).

Runs Preview in-process (MESH_JOB_INLINE=1) so LLM usage accum is visible.
Does not call publish() / does not touch prod DB.
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

os.environ["MESH_JOB_INLINE"] = "1"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db, llm, preview_job  # noqa: E402
from app.relation_display import build_published_projection  # noqa: E402
from app.relation_decision_audit import build_human_report  # noqa: E402


def _resolve_slug(arg: str | None) -> str:
    con = db.connect()
    try:
        if arg:
            row = con.execute(
                "SELECT slug, period_label, status FROM issues WHERE slug=? OR period_label=?",
                (arg, arg),
            ).fetchone()
            if row:
                return row["slug"]
            # try dotted ↔ dashed
            alt = arg.replace(".", "-") if "." in arg else arg.replace("-", ".")
            row = con.execute(
                "SELECT slug, period_label, status FROM issues WHERE slug=? OR period_label=?",
                (alt, alt),
            ).fetchone()
            if row:
                return row["slug"]
            raise SystemExit(f"issue not found: {arg}")
        row = con.execute(
            """SELECT slug FROM issues
               WHERE slug='2026-8-27' OR period_label='2026.8.27'
                  OR period_label LIKE '2026.8.27%'
               ORDER BY updated_at DESC LIMIT 1"""
        ).fetchone()
        if not row:
            raise SystemExit("no issue matching 2026-8-27 / 2026.8.27")
        return row["slug"]
    finally:
        con.close()


def _issue_meta(slug: str) -> dict:
    con = db.connect()
    try:
        r = con.execute(
            """SELECT id, slug, period_label, status, updated_at, version,
                      (SELECT COUNT(*) FROM items WHERE issue_id=issues.id) AS n_items,
                      (SELECT COUNT(*) FROM sources WHERE issue_id=issues.id AND length(COALESCE(text,''))>0) AS n_sources_text,
                      (SELECT COUNT(*) FROM sources WHERE issue_id=issues.id AND length(COALESCE(text,''))>0 AND extracted=0) AS n_unextracted
               FROM issues WHERE slug=?""",
            (slug,),
        ).fetchone()
        return dict(r) if r else {}
    finally:
        con.close()


def _tier_counts(rels: list) -> dict:
    c = Counter()
    for rel in rels:
        if not isinstance(rel, dict):
            continue
        t = rel.get("decision_tier")
        c[str(t) if t is not None else "null"] += 1
    return dict(c)


def _projection_stats(draft: dict) -> dict:
    proj = build_published_projection(draft)
    d_rels = [r for r in (draft.get("relations") or []) if isinstance(r, dict)]
    p_rels = [r for r in (proj.get("relations") or []) if isinstance(r, dict)]
    return {
        "draft_n_relations": len(d_rels),
        "draft_tier_counts": _tier_counts(d_rels),
        "published_projection_n_relations": len(p_rels),
        "published_projection_tier_counts": _tier_counts(p_rels),
        "published_projection_titles": [(r.get("title") or "").strip() for r in p_rels],
        "reader_visible_in_draft": sum(1 for r in d_rels if r.get("reader_visible") or r.get("decision_tier") == "strong"),
    }


def main() -> None:
    slug_arg = sys.argv[1] if len(sys.argv) > 1 else None
    slug = _resolve_slug(slug_arg)
    meta = _issue_meta(slug)
    if not meta:
        raise SystemExit(f"missing issue {slug}")
    if (meta.get("status") or "") == "published":
        print(
            f"NOTE: status=published — Preview 只写 draft_json，不改线上稿。slug={slug}",
            flush=True,
        )

    out_dir = ROOT / "eval" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    print(f"==> PREVIEW START slug={slug} status={meta.get('status')} items={meta.get('n_items')} unextracted={meta.get('n_unextracted')}", flush=True)
    if int(meta.get("n_unextracted") or 0) > 0:
        raise SystemExit(f"REFUSE: {meta['n_unextracted']} sources unextracted — Preview will fail gate")

    llm.reset_usage_accum()
    t0 = time.time()
    phase_log: list[dict] = []
    last_phase = None
    last_msg = None

    st = preview_job.start(slug, "tmesh-preview-audit", force=True)
    print(f"claimed running={st.get('running')} err={st.get('error')!r} code={st.get('error_code')!r}", flush=True)
    if st.get("error") and not st.get("running"):
        raise SystemExit(f"preview start failed: {st.get('error')}")

    # wait up to ~90 min
    deadline = t0 + 90 * 60
    while time.time() < deadline:
        st = preview_job.get_state(slug)
        phase = st.get("phase")
        msg = st.get("message")
        if phase != last_phase or msg != last_msg:
            entry = {
                "t_s": round(time.time() - t0, 2),
                "phase": phase,
                "message": msg,
                "cur": st.get("cur"),
                "total": st.get("total"),
                "running": st.get("running"),
                "done": st.get("done"),
                "error": st.get("error"),
            }
            phase_log.append(entry)
            print(f"PHASE {entry}", flush=True)
            last_phase, last_msg = phase, msg
        if st.get("done"):
            break
        if st.get("error") and not st.get("running"):
            break
        time.sleep(5)
    else:
        raise SystemExit("TIMEOUT waiting for preview")

    elapsed_s = round(time.time() - t0, 2)
    usage = llm.take_usage_accum()

    # stage durations from phase_log transitions
    stage_durations = []
    for i, e in enumerate(phase_log):
        t_next = phase_log[i + 1]["t_s"] if i + 1 < len(phase_log) else elapsed_s
        stage_durations.append({
            "phase": e.get("phase"),
            "message": e.get("message"),
            "start_s": e.get("t_s"),
            "end_s": t_next,
            "duration_s": round(float(t_next) - float(e.get("t_s") or 0), 2),
        })

    con = db.connect()
    try:
        row = con.execute(
            "SELECT status, draft_json, published_json, updated_at FROM issues WHERE slug=?",
            (slug,),
        ).fetchone()
    finally:
        con.close()

    draft = json.loads((row["draft_json"] if row else None) or "{}")
    published_stored = json.loads((row["published_json"] if row else None) or "{}")
    audit = draft.get("_relation_decision_audit") or {}
    rels = draft.get("relations") or []
    human = build_human_report(audit, rels) if audit else {}
    proj_stats = _projection_stats(draft)
    stored_pub_rels = [r for r in (published_stored.get("relations") or []) if isinstance(r, dict)]

    anomalies = []
    if row and (row["status"] or "") == "published":
        anomalies.append("CRITICAL: status became published after Preview (should stay draft)")
    if int(proj_stats["draft_n_relations"] or 0) == 0:
        anomalies.append("draft relations = 0 after Preview")
    if audit.get("outcome_summary"):
        osu = audit["outcome_summary"]
    else:
        osu = human.get("outcome_summary") or {}
    n_cand = osu.get("n_candidates") or osu.get("n_candidates_for_decision")
    if n_cand and int(osu.get("n_draft_relations") or proj_stats["draft_n_relations"] or 0) == 0:
        anomalies.append(f"funnel drop to 0 draft (candidates={n_cand})")
    if proj_stats["published_projection_n_relations"] and any(
        (r.get("decision_tier") or "") != "strong" for r in stored_pub_rels
    ):
        anomalies.append("stored published_json contains non-strong relation (projection leak?)")
    for r in stored_pub_rels:
        if (r.get("decision_tier") or "") != "strong":
            anomalies.append(f"stored published tier leak: {r.get('title')} tier={r.get('decision_tier')}")
            break
    if usage.get("n_calls") in (None, 0):
        anomalies.append("LLM n_calls missing/0 — usage may not have been captured in-process")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": "tmesh",
        "slug": slug,
        "issue_before": meta,
        "issue_after_status": row["status"] if row else None,
        "issue_updated_at": row["updated_at"] if row else None,
        "constraints": {
            "no_manual_data_sync": True,
            "no_publish": True,
            "no_prod_touch": True,
            "preview_via": "preview_job.start(force=True) + MESH_JOB_INLINE=1",
        },
        "timing": {
            "total_s": elapsed_s,
            "phase_log": phase_log,
            "stage_durations_s": stage_durations,
            "job_final": {
                "done": st.get("done"),
                "error": st.get("error"),
                "error_code": st.get("error_code"),
                "message": st.get("message"),
                "phase": st.get("phase"),
                "log_tail": (st.get("log") or [])[-20:],
            },
        },
        "llm_usage": usage,
        "relation_audit": audit,
        "candidate_ledger": human.get("candidate_ledger") or audit.get("candidate_ledger") or [],
        "outcome_summary": osu,
        "ledger_lines": human.get("ledger_lines") or [],
        "projection": proj_stats,
        "stored_published_json_n_relations": len(stored_pub_rels),
        "stored_published_tier_counts": _tier_counts(stored_pub_rels),
        "anomalies": anomalies,
    }

    out_path = out_dir / f"tmesh_preview_audit_{slug}_{stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # also write a stable latest pointer
    latest = out_dir / f"tmesh_preview_audit_{slug}_latest.json"
    latest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("==> PREVIEW DONE", flush=True)
    print(json.dumps({
        "out": str(out_path),
        "total_s": elapsed_s,
        "llm_usage": usage,
        "outcome_summary": osu,
        "projection": proj_stats,
        "anomalies": anomalies,
        "status": row["status"] if row else None,
    }, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
