"""Relation Writer 判断账本：首写 → rewrite → hygiene → grounding → 近重。

不存完整 prompt/原文；只存可追因的摘要与原因码。
挂在 draft_json._relation_writer_audit。
"""
from __future__ import annotations

from typing import Any


def _clip(text: str, n: int = 120) -> str:
    s = (text or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def new_trace(candidate_id: str) -> dict[str, Any]:
    return {
        "candidate_id": (candidate_id or "").strip(),
        "events": [],
        "final": {},
    }


def append_event(trace: dict | None, kind: str, **payload: Any) -> dict:
    row = trace if isinstance(trace, dict) else new_trace(str(payload.get("candidate_id") or ""))
    ev = {"kind": kind}
    for k, v in payload.items():
        if k == "candidate_id":
            continue
        if isinstance(v, str) and k in ("title", "body", "bad_title", "bad_body", "new_title", "new_body"):
            ev[k] = _clip(v)
        else:
            ev[k] = v
    row.setdefault("events", []).append(ev)
    return row


def snapshot_final(rel: dict, *, outcome: str = "formed") -> dict[str, Any]:
    return {
        "outcome": outcome,
        "title": _clip(str(rel.get("title") or ""), 80),
        "body_empty": not bool((rel.get("body") or "").strip()),
        "n_details": len([d for d in (rel.get("details") or []) if str(d).strip()]),
        "ungrounded": bool(rel.get("_body_omitted_ungrounded")),
        "template_cleared": bool(rel.get("_body_omitted_template")),
        "rewrite_rounds": rel.get("_writer_rewrite_rounds") or 0,
        "rewrite_fields": list(rel.get("_writer_field_rewrite") or []),
        "suspected_duplicate": bool(rel.get("suspected_duplicate")),
        "duplicate_of": rel.get("duplicate_of") or None,
    }


def attach_trace_to_rel(rel: dict, trace: dict) -> dict:
    rel = dict(rel)
    rel["_writer_trace"] = trace
    return rel


def finalize_writer_audit(
    relations: list[dict],
    *,
    skipped: list[dict] | None = None,
    demoted: list[dict] | None = None,
) -> dict[str, Any]:
    """汇总 draft 级账本。"""
    rows: list[dict] = []
    n_empty = 0
    n_ungrounded = 0
    n_rewrite = 0
    n_dup = 0
    for r in relations or []:
        if not isinstance(r, dict):
            continue
        trace = r.get("_writer_trace")
        if not isinstance(trace, dict):
            trace = new_trace(str(r.get("candidate_id") or ""))
        final = snapshot_final(r, outcome="formed")
        trace["final"] = final
        rows.append({
            "candidate_id": trace.get("candidate_id") or r.get("candidate_id"),
            "title": final["title"],
            "events": list(trace.get("events") or [])[-12:],
            "final": final,
        })
        if final["body_empty"]:
            n_empty += 1
        if final["ungrounded"]:
            n_ungrounded += 1
        if final["rewrite_rounds"]:
            n_rewrite += 1
        if final["suspected_duplicate"] or r.get("_draft_warning") == "suspected_duplicate":
            n_dup += 1

    for r in demoted or []:
        if not isinstance(r, dict):
            continue
        trace = r.get("_writer_trace") if isinstance(r.get("_writer_trace"), dict) else new_trace(
            str(r.get("candidate_id") or "")
        )
        final = snapshot_final(r, outcome="demoted_duplicate")
        trace["final"] = final
        rows.append({
            "candidate_id": trace.get("candidate_id") or r.get("candidate_id"),
            "title": final["title"],
            "events": list(trace.get("events") or [])[-12:],
            "final": final,
        })
        n_dup += 1

    skip_rows = []
    for s in skipped or []:
        if not isinstance(s, dict):
            continue
        skip_rows.append({
            "candidate_id": s.get("candidate_id"),
            "reason": s.get("reason"),
        })

    return {
        "version": 1,
        "n_rows": len(rows),
        "n_skipped": len(skip_rows),
        "summary": {
            "n_formed": len(relations or []),
            "n_demoted_duplicate": len(demoted or []),
            "n_body_empty": n_empty,
            "n_body_ungrounded": n_ungrounded,
            "n_rewritten": n_rewrite,
            "n_duplicate_marked": n_dup,
        },
        "rows": rows,
        "skipped": skip_rows,
    }


def merge_writer_audit(draft: dict, audit: dict) -> dict:
    draft = dict(draft)
    draft["_relation_writer_audit"] = audit
    return draft
