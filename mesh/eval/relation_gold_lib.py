"""Relation Gold v2：加载、schema 校验、lexical baseline（非 Claim Validity）。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
GOLD_V2 = EVAL_DIR / "relation_gold_v2.jsonl"
BASELINE_PATH = EVAL_DIR / "reports" / "relation_gold_v2_baseline.json"

REQUIRED_TOP = ("id", "title", "notes", "expect")
EXPECTS = frozenset(
    {
        "keep",
        "drop",
        "team_via_evidence",
        "fingerprint_stable",
        "claim_valid",
        "claim_invalid",
    }
)
CLAIM_EXPECTS = frozenset({"claim_valid", "claim_invalid"})
FINGERPRINT_EXPECT = "fingerprint_stable"


def load_gold(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or GOLD_V2
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{p.name}:{line_no}: invalid JSON: {e}") from e
        rows.append(row)
    return rows


def validate_case(case: dict[str, Any], *, line_no: int | None = None) -> list[str]:
    """返回错误列表；空列表 = 通过。"""
    loc = f"line {line_no}" if line_no is not None else case.get("id", "?")
    errs: list[str] = []
    if not isinstance(case, dict):
        return [f"{loc}: case must be object"]

    for k in REQUIRED_TOP:
        if k not in case or case[k] in (None, ""):
            errs.append(f"{loc}: missing required field `{k}`")

    expect = case.get("expect")
    if expect not in EXPECTS:
        errs.append(f"{loc}: expect must be one of {sorted(EXPECTS)}, got {expect!r}")

    # 统一键存在（允许空）；避免 smoke 特判失控
    for k in ("rel", "items", "claim"):
        if k not in case:
            errs.append(f"{loc}: missing unified field `{k}` (use null/[]/{{}} as needed)")

    if expect == FINGERPRINT_EXPECT:
        for k in ("title_a", "title_b", "teams_a", "teams_b"):
            if k not in case:
                errs.append(f"{loc}: fingerprint case needs `{k}`")
    else:
        rel = case.get("rel")
        items = case.get("items")
        if not isinstance(rel, dict):
            errs.append(f"{loc}: rel must be object for expect={expect}")
        if not isinstance(items, list):
            errs.append(f"{loc}: items must be list for expect={expect}")

    if expect in CLAIM_EXPECTS:
        claim = case.get("claim")
        if not isinstance(claim, dict):
            errs.append(f"{loc}: claim must be object for claim_* expect")
        else:
            if "valid" not in claim or not isinstance(claim["valid"], bool):
                errs.append(f"{loc}: claim.valid must be bool")
            if not (claim.get("reason") or "").strip():
                errs.append(f"{loc}: claim.reason required")
            want = expect == "claim_valid"
            if claim.get("valid") is not want:
                errs.append(
                    f"{loc}: claim.valid={claim.get('valid')} inconsistent with expect={expect}"
                )

    # optional annotation assets
    for opt in ("expect_type", "expect_tier"):
        if opt in case and case[opt] is not None and not isinstance(case[opt], str):
            errs.append(f"{loc}: `{opt}` must be string or null")

    return errs


def validate_gold(cases: list[dict[str, Any]] | None = None) -> list[str]:
    cases = cases if cases is not None else load_gold()
    errs: list[str] = []
    ids: set[str] = set()
    for i, c in enumerate(cases, 1):
        errs.extend(validate_case(c, line_no=i))
        cid = c.get("id")
        if isinstance(cid, str):
            if cid in ids:
                errs.append(f"line {i}: duplicate id {cid}")
            ids.add(cid)
    return errs


def lexical_body_grounded(rel: dict) -> bool:
    """当前系统 line_grounded；不得等同于 claim_valid。"""
    from app.relation_verify import line_grounded

    body = (rel.get("body") or "").strip()
    if not body:
        return False
    return bool(line_grounded(body, rel))


def gate_snapshot(rel: dict, items: list[dict]) -> dict[str, Any]:
    from app.relation_gate import filter_ungrounded_relations, relation_fails_grounding

    fails = relation_fails_grounding(rel, items)
    draft, dropped = filter_ungrounded_relations({"relations": [rel]}, items)
    return {
        "fails_grounding": list(fails),
        "gate_kept": bool(draft.get("relations")),
        "dropped_titles": list(dropped),
    }


def build_baseline(cases: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    cases = cases if cases is not None else load_gold()
    from app.relation_claim_check import check_relation_claim

    rows = []
    for c in cases:
        if c.get("expect") not in CLAIM_EXPECTS:
            continue
        rel = c.get("rel") or {}
        items = c.get("items") or []
        lg = lexical_body_grounded(rel)
        gate = gate_snapshot(rel, items)
        claim = check_relation_claim(rel, items)
        rows.append(
            {
                "id": c["id"],
                "title": c.get("title"),
                "expect": c["expect"],
                "gold_claim_valid": bool((c.get("claim") or {}).get("valid")),
                "gold_claim_invalid": not bool((c.get("claim") or {}).get("valid")),
                "current_line_grounded": lg,
                "current_gate_result": gate,
                "current_claim_check": {
                    "claim_verdict": claim.get("claim_verdict"),
                    "claim_reason_code": claim.get("claim_reason_code"),
                    "claim_strength": claim.get("claim_strength"),
                    "evidence_strength": claim.get("evidence_strength"),
                    "model": claim.get("model"),
                },
                "note": "line_grounded≠claim_verdict; claim_check=rule_v1",
            }
        )
    return {
        "gold_file": GOLD_V2.name,
        "n_claim_cases": len(rows),
        "cases": rows,
    }


def write_baseline(path: Path | None = None) -> Path:
    out = path or BASELINE_PATH
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = build_baseline()
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out
