#!/usr/bin/env python3
"""纯诊断：relation_decisions LLM 调用全链路（复现调用，非 preview 当场日志）。

Preview 未持久化原始 response；本脚本用相同 issue 数据重新打一次 API，
对比 parser 前后 decisions 数量，判断漏答发生在模型还是解析。

用法:
  python deploy/_diag_relation_decision_llm.py --slug 2026-8-17
  python deploy/_diag_relation_decision_llm.py --slug 2026-8-17 --out eval/reports/diag_llm_2026-8-17.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import llm
from app.llm import (
    RELATION_DECISIONS_MAX_TOKENS,
    missing_decision_ids,
    parse_json,
    prepare_relation_decisions_messages,
    provider_info,
)
from app import db
from app.providers import get_provider
from app.relation_candidates import prepare_draft_bundle
from app.relation_decision import assign_candidate_ids


def _call_raw(system: str, user: str, max_tokens: int) -> dict:
    prov = get_provider()
    if hasattr(prov, "complete_detail"):
        detail = prov.complete_detail(system, user, max_tokens=max_tokens)
        return {
            "content": detail.get("content") or "",
            "finish_reason": detail.get("finish_reason"),
            "usage": detail.get("usage"),
            "model": detail.get("model"),
            "raw_response": detail.get("raw_response"),
            "request_payload": detail.get("request_payload"),
        }
    content = prov.complete(system, user, max_tokens=max_tokens)
    return {
        "content": content,
        "finish_reason": None,
        "usage": None,
        "model": getattr(prov, "model", None),
        "raw_response": None,
        "request_payload": {
            "model": getattr(prov, "model", None),
            "max_tokens": max_tokens,
            "note": "provider 无 complete_detail，仅 content",
        },
    }


def _count_decisions_in_raw_text(text: str) -> int | None:
    try:
        obj = parse_json(text)
    except Exception:
        return None
    if isinstance(obj, dict):
        arr = obj.get("relation_decisions")
        if isinstance(arr, list):
            return len(arr)
    return None


def diagnose(*, slug: str) -> dict:
    con = db.connect()
    issue = con.execute("SELECT id, draft_json FROM issues WHERE slug=?", (slug,)).fetchone()
    if not issue:
        raise SystemExit(f"issue not found: {slug}")
    draft = json.loads(issue["draft_json"] or "{}")
    stored_audit = draft.get("_relation_decision_audit") or {}
    bundle = prepare_draft_bundle(con, int(issue["id"]), slug)
    con.close()

    cands = assign_candidate_ids(bundle["relation_candidates"])
    system, user, prep_meta = prepare_relation_decisions_messages(
        cands, bundle["team_cards"],
    )
    pinfo = provider_info()
    max_tokens = RELATION_DECISIONS_MAX_TOKENS

    api = _call_raw(system, user, max_tokens)
    raw_text = api.get("content") or ""

    parser_error = None
    parsed_obj: dict | list | None = None
    try:
        parsed_obj = parse_json(raw_text)
    except Exception as e:
        parser_error = str(e)

    pre_parser_count = _count_decisions_in_raw_text(raw_text)
    post_parser_decisions: list[dict] = []
    if isinstance(parsed_obj, dict):
        post_parser_decisions = [
            d for d in (parsed_obj.get("relation_decisions") or [])
            if isinstance(d, dict)
        ]

    # call_json_compliant 路径（含 forbidden 修补，通常不改变 relation_decisions 条数）
    compliant_obj: dict | None = None
    compliant_error = None
    try:
        compliant_obj = llm.call_json_compliant(system, user, max_tokens=max_tokens)
    except Exception as e:
        compliant_error = str(e)
    compliant_decisions = []
    if isinstance(compliant_obj, dict):
        compliant_decisions = [
            d for d in (compliant_obj.get("relation_decisions") or [])
            if isinstance(d, dict)
        ]

    missing = missing_decision_ids(cands, post_parser_decisions)

    return {
        "slug": slug,
        "note": (
            "Preview 未保存当场 raw response；此为同 issue 数据的复现调用。"
            " stored_preview 字段来自 draft_json 中上次 preview 写入的 audit。"
        ),
        "stored_preview": {
            "n_candidates_for_decision": stored_audit.get("n_candidates_for_decision"),
            "n_llm_decisions": stored_audit.get("n_llm_decisions"),
            "n_decision_missing": stored_audit.get("n_decision_missing")
            or (stored_audit.get("decision_coverage") or {}).get("missing_ids"),
            "ledger_rows": len(stored_audit.get("candidate_ledger") or stored_audit.get("rows") or []),
        },
        "input": {
            "n_candidates": len(cands),
            "candidate_ids": [c.get("candidate_id") for c in cands],
            **prep_meta,
        },
        "request": {
            "provider": pinfo,
            "model": api.get("model") or pinfo.get("model"),
            "max_tokens": max_tokens,
            "temperature": None,
            "response_format": None,
            "json_mode": "post-parse via parse_json / call_json_compliant（请求体无 response_format）",
            "system_chars": len(system),
            "user_chars": len(user),
            "request_payload": api.get("request_payload"),
        },
        "response": {
            "finish_reason": api.get("finish_reason"),
            "usage": api.get("usage"),
            "raw_content_chars": len(raw_text),
            "raw_content": raw_text,
            "raw_response": api.get("raw_response"),
        },
        "parser": {
            "parse_error": parser_error,
            "pre_parser_decision_count_in_raw_json": pre_parser_count,
            "post_parser_decision_count": len(post_parser_decisions),
            "post_parser_candidate_ids": [
                (d.get("candidate_id") or "").strip() for d in post_parser_decisions
            ],
            "missing_decision_ids": missing,
            "compliant_path_error": compliant_error,
            "compliant_path_decision_count": len(compliant_decisions),
            "parser_vs_compliant_same_count": (
                len(post_parser_decisions) == len(compliant_decisions)
                if not compliant_error
                else None
            ),
        },
        "verdict": (
            "LLM_RAW_PARTIAL"
            if pre_parser_count is not None and pre_parser_count < len(cands)
            else (
                "PARSER_LOSS"
                if pre_parser_count is not None
                and pre_parser_count > len(post_parser_decisions)
                else (
                    "LLM_RAW_OK"
                    if pre_parser_count == len(cands) and not missing
                    else "INCONCLUSIVE"
                )
            )
        ),
        "prompt": {
            "system": system,
            "user": user,
        },
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", default="2026-8-17")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    report = diagnose(slug=args.slug)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    out = args.out or ROOT / "eval" / "reports" / f"diag_relation_decision_llm_{args.slug}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    main()
