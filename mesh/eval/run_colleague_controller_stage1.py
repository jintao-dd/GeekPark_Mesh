"""Stage 1 Colleague Controller Gate.

Metrics:
  wrong_route
  retrieval_when_unneeded
  missed_grounding
  clarification_accuracy
  router_llm_budget

Usage:
  python -m eval.run_colleague_controller_stage1
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
    return SessionContextState(**{k: v for k, v in raw.items() if k in SessionContextState.__dataclass_fields__})


def _run_one(case: dict) -> dict:
    st = _session(case.get("session"))
    mock_payload = case.get("mock_llm")
    expect_llm = bool(case.get("expect_router_llm"))

    if expect_llm and mock_payload:
        with mock.patch("app.llm.call", return_value=dict(mock_payload)):
            with mock.patch("app.llm.model_for_task", return_value="mock-controller"):
                d = ctrl.decide(case["text"], st, allow_llm=True)
    else:
        d = ctrl.decide(case["text"], st, allow_llm=False)

    expect_mode = case["expect_mode"]
    expect_g = bool(case["expect_grounding"])
    wrong_route = d.mode != expect_mode
    # content may surface as conversation in some paths — already mapped to content in soft/fast
    retrieval_when_unneeded = (not expect_g) and ctrl.will_retrieve(d)
    missed_grounding = expect_g and not ctrl.will_retrieve(d)
    clarification_ok = True
    if expect_mode == "clarify":
        clarification_ok = bool(d.needs_clarification) and d.mode == "clarify" and not d.needs_grounding
    router_budget_ok = True
    if not expect_llm:
        router_budget_ok = not d.router_llm_used
    else:
        router_budget_ok = bool(d.router_llm_used) and d.source in ("llm", "llm_fallback")

    ok = (
        not wrong_route
        and not retrieval_when_unneeded
        and not missed_grounding
        and clarification_ok
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
        "router_budget_ok": router_budget_ok,
        "got": {
            "mode": d.mode,
            "needs_grounding": d.needs_grounding,
            "needs_clarification": d.needs_clarification,
            "response_mode": d.response_mode,
            "source": d.source,
            "router_llm_used": d.router_llm_used,
            "rewritten_query": d.rewritten_query,
        },
        "expect": {
            "mode": expect_mode,
            "grounding": expect_g,
            "router_llm": expect_llm,
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
    clarify_ok = sum(1 for r in results if r["expect"]["mode"] == "clarify" and r["clarification_ok"])
    budget_bad = sum(1 for r in results if not r["router_budget_ok"])
    passed = sum(1 for r in results if r["ok"])

    summary = {
        "n": len(results),
        "pass": passed,
        "pass_rate": round(passed / n, 4),
        "wrong_route": wrong,
        "wrong_route_rate": round(wrong / n, 4),
        "retrieval_when_unneeded": unneeded,
        "retrieval_when_unneeded_rate": round(unneeded / n, 4),
        "missed_grounding": missed,
        "missed_grounding_rate": round(missed / n, 4),
        "clarification_accuracy": round(clarify_ok / clarify_n, 4),
        "router_budget_violations": budget_bad,
        "gate": {
            "wrong_route_rate_max": 0.15,
            "retrieval_when_unneeded_max": 0,
            "missed_grounding_max": 0,
            "clarification_accuracy_min": 0.85,
            "router_budget_violations_max": 0,
        },
    }
    gate = summary["gate"]
    summary["gate_pass"] = (
        summary["wrong_route_rate"] <= gate["wrong_route_rate_max"]
        and summary["retrieval_when_unneeded"] <= gate["retrieval_when_unneeded_max"]
        and summary["missed_grounding"] <= gate["missed_grounding_max"]
        and summary["clarification_accuracy"] >= gate["clarification_accuracy_min"]
        and summary["router_budget_violations"] <= gate["router_budget_violations_max"]
    )

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "cases": results}
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Colleague Controller Stage 1 Gate",
        "",
        f"- pass_rate: **{summary['pass_rate']}** ({passed}/{len(results)})",
        f"- wrong_route: {wrong} (rate {summary['wrong_route_rate']})",
        f"- retrieval_when_unneeded: {unneeded}",
        f"- missed_grounding: {missed}",
        f"- clarification_accuracy: {summary['clarification_accuracy']}",
        f"- router_budget_violations: {budget_bad}",
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
                f"- `{r['id']}` {r['text']!r} expect={r['expect']['mode']} got={r['got']['mode']} "
                f"g={r['got']['needs_grounding']} src={r['got']['source']}"
            )
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"wrote {OUT_JSON}")
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
