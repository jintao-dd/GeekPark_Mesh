"""Stage 1 Gate — Colleague Controller Semantic Decision Layer.

Focus: hard boundaries + semantic understanding (mocked LLM for CI).
Not: regex phrase coverage.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import colleague_controller as ctrl
from app.agent.session_state import SessionContextState

SCENARIOS = ROOT / "eval" / "colleague_controller_stage1.jsonl"
OUT_JSON = ROOT / "eval" / "reports" / "COLLEAGUE_CONTROLLER_STAGE1.json"
OUT_MD = ROOT / "eval" / "reports" / "COLLEAGUE_CONTROLLER_STAGE1.md"


def _load() -> list[dict]:
    rows = []
    for line in SCENARIOS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _session(raw: dict | None) -> SessionContextState:
    if not raw:
        return SessionContextState()
    known = SessionContextState.__dataclass_fields__
    return SessionContextState(**{k: v for k, v in raw.items() if k in known})


def _run_one(case: dict) -> dict:
    st = _session(case.get("session"))
    mock_payload = case.get("mock_llm")
    expect_llm = bool(case.get("expect_router_llm"))

    if expect_llm and mock_payload:
        with mock.patch("app.llm.call", return_value=dict(mock_payload)) as m:
            with mock.patch("app.llm.model_for_task", return_value="mock-controller"):
                d = ctrl.decide(case["text"], st, allow_llm=True)
            call_count = m.call_count
    else:
        d = ctrl.decide(case["text"], st, allow_llm=False)
        call_count = 0

    expect_mode = case["expect_mode"]
    expect_g = bool(case["expect_grounding"])
    wrong_route = d.mode != expect_mode
    retrieval_when_unneeded = (not expect_g) and ctrl.will_retrieve(d)
    missed_grounding = expect_g and not ctrl.will_retrieve(d)
    clarification_ok = True
    if expect_mode == "clarify":
        clarification_ok = bool(d.needs_clarification) and not d.needs_grounding
    resp_ok = True
    if case.get("expect_response_mode"):
        resp_ok = d.response_mode == case["expect_response_mode"]
    context_error = False
    if "context" in (case.get("tags") or []) and expect_mode == "followup":
        # followup 必须带着可检索改写，且不把 memory 当事实源以外的东西
        context_error = not bool(d.rewritten_query)
    router_budget_ok = (call_count <= 1) and (
        (expect_llm and d.router_llm_used and call_count == 1)
        or ((not expect_llm) and (not d.router_llm_used) and call_count == 0)
    )

    ok = (
        not wrong_route
        and not retrieval_when_unneeded
        and not missed_grounding
        and clarification_ok
        and resp_ok
        and not context_error
        and router_budget_ok
    )
    return {
        "id": case["id"],
        "text": case["text"],
        "ok": ok,
        "wrong_route": wrong_route,
        "retrieval_when_unneeded": retrieval_when_unneeded,
        "missed_grounding": missed_grounding,
        "clarification_ok": clarification_ok,
        "response_mode_ok": resp_ok,
        "context_error": context_error,
        "router_budget_ok": router_budget_ok,
        "controller_calls": call_count,
        "got": {
            "mode": d.mode,
            "needs_grounding": d.needs_grounding,
            "needs_clarification": d.needs_clarification,
            "response_mode": d.response_mode,
            "source": d.source,
            "router_llm_used": d.router_llm_used,
            "rewritten_query": d.rewritten_query,
            "confidence": d.confidence,
        },
        "expect": {
            "mode": expect_mode,
            "grounding": expect_g,
            "router_llm": expect_llm,
            "response_mode": case.get("expect_response_mode"),
        },
        "tags": case.get("tags") or [],
    }


def main() -> int:
    rows = _load()
    results = [_run_one(c) for c in rows]
    n = len(results) or 1
    wrong = sum(1 for r in results if r["wrong_route"])
    unneeded = sum(1 for r in results if r["retrieval_when_unneeded"])
    missed = sum(1 for r in results if r["missed_grounding"])
    clarify_n = sum(1 for r in results if r["expect"]["mode"] == "clarify") or 1
    clarify_ok = sum(
        1 for r in results if r["expect"]["mode"] == "clarify" and r["clarification_ok"]
    )
    ctx_err = sum(1 for r in results if r["context_error"])
    budget_bad = sum(1 for r in results if not r["router_budget_ok"])
    passed = sum(1 for r in results if r["ok"])
    hard_n = sum(1 for r in results if "hard" in r["tags"])
    hard_llm = sum(
        1 for r in results if "hard" in r["tags"] and r["got"]["router_llm_used"]
    )

    summary = {
        "n": len(results),
        "pass": passed,
        "pass_rate": round(passed / n, 4),
        "wrong_route": wrong,
        "wrong_route_rate": round(wrong / n, 4),
        "retrieval_when_unneeded": unneeded,
        "missed_grounding": missed,
        "clarification_accuracy": round(clarify_ok / clarify_n, 4),
        "context_error": ctx_err,
        "router_budget_violations": budget_bad,
        "hard_path_controller_llm_calls": hard_llm,
        "gate": {
            "wrong_route_rate_max": 0.1,
            "retrieval_when_unneeded_max": 0,
            "missed_grounding_max": 0,
            "clarification_accuracy_min": 0.85,
            "context_error_max": 0,
            "router_budget_violations_max": 0,
            "hard_path_controller_llm_max": 0,
        },
    }
    g = summary["gate"]
    summary["gate_pass"] = (
        summary["wrong_route_rate"] <= g["wrong_route_rate_max"]
        and summary["retrieval_when_unneeded"] <= g["retrieval_when_unneeded_max"]
        and summary["missed_grounding"] <= g["missed_grounding_max"]
        and summary["clarification_accuracy"] >= g["clarification_accuracy_min"]
        and summary["context_error"] <= g["context_error_max"]
        and summary["router_budget_violations"] <= g["router_budget_violations_max"]
        and summary["hard_path_controller_llm_calls"] <= g["hard_path_controller_llm_max"]
    )

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        "# Colleague Controller Stage 1 · Semantic Decision Gate",
        "",
        f"- pass_rate: **{summary['pass_rate']}** ({passed}/{len(results)})",
        f"- wrong_route: {wrong}",
        f"- retrieval_when_unneeded: {unneeded}",
        f"- missed_grounding: {missed}",
        f"- clarification_accuracy: {summary['clarification_accuracy']}",
        f"- context_error: {ctx_err}",
        f"- hard_path Controller LLM calls: {hard_llm} / hard_cases={hard_n}",
        f"- gate_pass: **{summary['gate_pass']}**",
        "",
        "## Failures",
    ]
    fails = [r for r in results if not r["ok"]]
    if not fails:
        lines.append("(none)")
    else:
        for r in fails:
            lines.append(
                f"- `{r['id']}` {r['text']!r} expect={r['expect']['mode']} "
                f"got={r['got']['mode']} src={r['got']['source']}"
            )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
