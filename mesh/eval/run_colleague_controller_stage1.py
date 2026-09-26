"""Stage 1 Gate A/B — Colleague Controller.

Gate A (CI): deterministic — hard boundary / schema / wiring / fallback（mock LLM）
Gate B (tmesh): semantic acceptance — 真实 Controller LLM

Usage:
  python -m eval.run_colleague_controller_stage1          # Gate A
  python -m eval.run_colleague_controller_stage1 --gate B # Gate B (needs live LLM)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.agent import colleague_controller as ctrl
from app.agent.session_state import SessionContextState

SCENARIOS = ROOT / "eval" / "colleague_controller_stage1.jsonl"
OUT_DIR = ROOT / "eval" / "reports"


def _load(gate: str) -> list[dict]:
    rows = []
    for line in SCENARIOS.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if str(row.get("gate") or "A").upper() == gate.upper():
            rows.append(row)
    return rows


def _session(raw: dict | None) -> SessionContextState:
    if not raw:
        return SessionContextState()
    known = SessionContextState.__dataclass_fields__
    return SessionContextState(**{k: v for k, v in raw.items() if k in known})


def _score(case: dict, d: ctrl.ControllerDecision, call_count: int) -> dict:
    expect_mode = case["expect_mode"]
    expect_g = bool(case["expect_grounding"])
    expect_llm = bool(case.get("expect_router_llm"))
    expect_resp = case.get("expect_response_mode")
    expect_source = case.get("expect_source")

    mode_ok = d.mode == expect_mode
    g_ok = bool(d.needs_grounding) == expect_g
    resp_ok = True if not expect_resp else d.response_mode == expect_resp
    # Controller 自身：mode + grounding +（若标注）response_mode
    decision_ok = mode_ok and g_ok and resp_ok

    wrong_route = not mode_ok
    retrieval_when_unneeded = (not expect_g) and ctrl.will_retrieve(d)
    missed_grounding = expect_g and not ctrl.will_retrieve(d)
    clarification_ok = True
    if expect_mode == "clarify":
        clarification_ok = bool(d.needs_clarification) and not d.needs_grounding
    context_error = False
    if "context" in (case.get("tags") or []) and expect_mode == "followup":
        context_error = not bool(d.rewritten_query)
    source_ok = True if not expect_source else d.source == expect_source
    router_budget_ok = call_count <= 1 and (
        (expect_llm and d.router_llm_used and call_count == 1)
        or ((not expect_llm) and (not d.router_llm_used) and call_count == 0)
    )
    runtime_ok = (
        not retrieval_when_unneeded
        and not missed_grounding
        and clarification_ok
        and not context_error
        and router_budget_ok
        and source_ok
    )
    ok = decision_ok and runtime_ok

    return {
        "id": case["id"],
        "text": case["text"],
        "ok": ok,
        "decision_ok": decision_ok,
        "runtime_ok": runtime_ok,
        "mode_ok": mode_ok,
        "needs_grounding_ok": g_ok,
        "response_mode_ok": resp_ok,
        "wrong_route": wrong_route,
        "retrieval_when_unneeded": retrieval_when_unneeded,
        "missed_grounding": missed_grounding,
        "clarification_ok": clarification_ok,
        "context_error": context_error,
        "router_budget_ok": router_budget_ok,
        "controller_calls": call_count,
        "controller_latency_ms": d.controller_latency_ms,
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
            "response_mode": expect_resp,
            "source": expect_source,
        },
        "tags": case.get("tags") or [],
    }


def _run_one(case: dict, *, live: bool) -> dict:
    st = _session(case.get("session"))
    force_no = bool(case.get("force_no_llm"))
    expect_llm = bool(case.get("expect_router_llm"))
    mock_payload = case.get("mock_llm")

    if force_no or (not live and not expect_llm):
        d = ctrl.decide(case["text"], st, allow_llm=False)
        return _score(case, d, 0)

    if not live and expect_llm and mock_payload:
        with mock.patch("app.llm.call", return_value=dict(mock_payload)) as m:
            with mock.patch("app.llm.model_for_task", return_value="mock-controller"):
                d = ctrl.decide(case["text"], st, allow_llm=True)
            return _score(case, d, m.call_count)

    # live Gate B
    t0 = time.perf_counter()
    d = ctrl.decide(case["text"], st, allow_llm=True)
    if not d.controller_latency_ms:
        d.controller_latency_ms = round((time.perf_counter() - t0) * 1000.0, 1)
    calls = 1 if d.router_llm_used else 0
    return _score(case, d, calls)


def _summarize(results: list[dict], gate: str) -> dict:
    n = len(results) or 1
    passed = sum(1 for r in results if r["ok"])
    mode_ok = sum(1 for r in results if r["mode_ok"])
    g_ok = sum(1 for r in results if r["needs_grounding_ok"])
    resp_n = sum(1 for r in results if r["expect"].get("response_mode")) or 1
    resp_ok = sum(
        1 for r in results if r["expect"].get("response_mode") and r["response_mode_ok"]
    )
    decision_ok = sum(1 for r in results if r["decision_ok"])
    wrong = sum(1 for r in results if r["wrong_route"])
    unneeded = sum(1 for r in results if r["retrieval_when_unneeded"])
    missed = sum(1 for r in results if r["missed_grounding"])
    ctx_err = sum(1 for r in results if r["context_error"])
    budget_bad = sum(1 for r in results if not r["router_budget_ok"])
    hard_llm = sum(
        1 for r in results if "hard" in r["tags"] and r["got"]["router_llm_used"]
    )
    lat = [r["controller_latency_ms"] for r in results if r["controller_latency_ms"]]
    summary = {
        "gate": gate,
        "n": len(results),
        "pass": passed,
        "pass_rate": round(passed / n, 4),
        "controller_decision_accuracy": round(decision_ok / n, 4),
        "mode_accuracy": round(mode_ok / n, 4),
        "needs_grounding_accuracy": round(g_ok / n, 4),
        "response_mode_accuracy": round(resp_ok / resp_n, 4),
        "wrong_route": wrong,
        "wrong_route_rate": round(wrong / n, 4),
        "retrieval_when_unneeded": unneeded,
        "missed_grounding": missed,
        "context_error": ctx_err,
        "router_budget_violations": budget_bad,
        "hard_path_controller_llm_calls": hard_llm,
        "controller_latency_ms_avg": round(sum(lat) / len(lat), 1) if lat else 0,
    }
    if gate == "A":
        summary["gate_pass"] = (
            summary["pass_rate"] >= 1.0
            and summary["retrieval_when_unneeded"] == 0
            and summary["missed_grounding"] == 0
            and summary["hard_path_controller_llm_calls"] == 0
            and summary["router_budget_violations"] == 0
            and summary["controller_decision_accuracy"] >= 1.0
        )
    else:
        summary["gate_pass"] = (
            summary["controller_decision_accuracy"] >= 0.8
            and summary["mode_accuracy"] >= 0.8
            and summary["needs_grounding_accuracy"] >= 0.85
            and summary["retrieval_when_unneeded"] == 0
            and summary["missed_grounding"] <= 1
            and summary["hard_path_controller_llm_calls"] == 0
            and summary["wrong_route_rate"] <= 0.2
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gate", choices=["A", "B"], default="A")
    args = ap.parse_args(argv)
    gate = args.gate
    live = gate == "B"
    rows = _load(gate)
    if not rows:
        print(f"no scenarios for gate {gate}")
        return 2
    results = [_run_one(c, live=live) for c in rows]
    summary = _summarize(results, gate)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = f"COLLEAGUE_CONTROLLER_STAGE1_GATE_{gate}"
    (OUT_DIR / f"{stem}.json").write_text(
        json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    fails = [r for r in results if not r["ok"]]
    lines = [
        f"# Colleague Controller Stage 1 · Gate {gate}",
        "",
        f"- gate_pass: **{summary['gate_pass']}**",
        f"- controller_decision_accuracy: {summary['controller_decision_accuracy']}",
        f"- mode_accuracy: {summary['mode_accuracy']}",
        f"- needs_grounding_accuracy: {summary['needs_grounding_accuracy']}",
        f"- response_mode_accuracy: {summary['response_mode_accuracy']}",
        f"- wrong_route_rate: {summary['wrong_route_rate']}",
        f"- retrieval_when_unneeded: {summary['retrieval_when_unneeded']}",
        f"- missed_grounding: {summary['missed_grounding']}",
        f"- hard Controller LLM: {summary['hard_path_controller_llm_calls']}",
        "",
        "## Failures",
    ]
    if not fails:
        lines.append("(none)")
    else:
        for r in fails:
            lines.append(
                f"- `{r['id']}` {r['text']!r} expect={r['expect']['mode']} "
                f"got={r['got']['mode']} g={r['got']['needs_grounding']} src={r['got']['source']}"
            )
    (OUT_DIR / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    # keep legacy filename for Gate A
    if gate == "A":
        (OUT_DIR / "COLLEAGUE_CONTROLLER_STAGE1.json").write_text(
            json.dumps({"summary": summary, "cases": results}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (OUT_DIR / "COLLEAGUE_CONTROLLER_STAGE1.md").write_text(
            "\n".join(lines) + "\n", encoding="utf-8"
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["gate_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
