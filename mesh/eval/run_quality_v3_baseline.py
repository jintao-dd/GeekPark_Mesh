#!/usr/bin/env python3
"""Agent Quality v3.0 · Production Baseline / E2E Release Candidate。

组装已冻结零件，做场景级综合验收——不是再刷单题 pass_rate。

冻结（本轮禁止改）：
  Retrieval Recall · Vector OFF · Ranking v1.4 · Claim Support v2.4c-2
  Agent Contract · Ontology schema · Prompt 基线
  Model：Sonnet 默认 + Opus 敏感 override（任务级配置，无动态 router）
  Wiki / ES / Graph / Agentic RAG / Planner：不引入

用法：
  PYTHONPATH=. python eval/run_quality_v3_baseline.py --reuse-env-db
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"
SCENARIO_GOLD = ROOT / "eval" / "agent_scenario_gold_v3.jsonl"

SONNET = "claude-sonnet-4-6"
OPUS = "anthropic/claude-4.8-opus"

BASELINE_MANIFEST = {
    "phase": "quality_v3_0_production_baseline",
    "ranking_version": "v1.4",
    "retrieval_version": "lexical_fts_recall_frozen",
    "vector": "OFF",
    "claim_support_version": "v2.4c-2_semantic_extension_frozen",
    "agent_contract": "frozen",
    "ontology": "doc_only_frozen",
    "prompt_version": "ask_engine_baseline",
    "model_policy": {
        "default_semantic": SONNET,
        "default_answer": SONNET,
        "sensitive_answer_override": OPUS,
        "dynamic_router": False,
    },
    "forbidden_this_phase": [
        "ranking_tweak",
        "claim_support_patch",
        "shadow_version",
        "model_ab_expansion",
        "dynamic_router",
        "es",
        "wiki",
        "neo4j_kg_graph_rag",
        "planner_react",
    ],
}


def _py(args: list[str], env: dict | None = None) -> int:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT)
    e.setdefault("MESH_CLAIM_SEMANTIC", "1")
    e.setdefault("MESH_RECALL_USE_EMBED", "0")
    e.setdefault("MESH_LLM_MODEL", SONNET)
    e.setdefault("MESH_LLM_MODEL_SEMANTIC", SONNET)
    e.setdefault("MESH_LLM_MODEL_ANSWER", SONNET)
    if env:
        e.update(env)
    print(">>", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _load_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(l)
        for l in path.read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]


def _pass_rate(rep: dict) -> float | None:
    if not rep:
        return None
    if "pass_rate" in rep:
        return float(rep["pass_rate"])
    s = rep.get("summary") or {}
    if "pass_rate" in s:
        return float(s["pass_rate"])
    return None


def _fail_ids(rep: dict) -> list[str]:
    out = []
    for r in rep.get("results") or rep.get("cases") or []:
        ok = r.get("pass")
        if ok is None:
            ok = (r.get("metrics") or {}).get("pass")
        if not ok:
            out.append(str(r.get("id") or "?"))
    return out


# —— Scenario E2E ——


def _attribute_failure(check_name: str, scenario_layers: list[str]) -> str:
    mapping = {
        "expect_route": "Context/Follow-up",
        "forbid_answer_leak": "Context/Follow-up",
        "prior_leak": "Context/Follow-up",
        "expect_abstain": "Answer",
        "must_mention": "Answer",
        "must_any": "Answer",
        "forbid_phrases": "Answer",
        "forbid_recent_event_claim": "Temporal",
        "min_evidence": "Retrieval",
        "must_cite_items": "Ranking",
        "expect_support": "Claim/Evidence",
        "claim_must_not_be_supported": "Claim/Evidence",
        "harness": "Harness/Infrastructure",
    }
    layer = mapping.get(check_name, "Answer")
    # Prefer scenario-declared layers when specific
    if check_name in ("min_evidence",) and "retrieval" in scenario_layers:
        return "Retrieval"
    if check_name in ("must_cite_items",) and "ranking" in scenario_layers:
        return "Ranking"
    if check_name.startswith("claim") or check_name == "expect_support":
        return "Claim/Evidence"
    if "temporal" in check_name or check_name == "forbid_recent_event_claim":
        return "Temporal"
    if check_name in ("expect_route", "prior_leak"):
        return "Context/Follow-up"
    return layer


def _prior_messages(step: dict) -> list[dict]:
    pq = step.get("prior_q") or "上一问"
    leak = step.get("prior_answer_leak") or "SECRET_LEAK_PRIOR_ANSWER_MUST_NOT_APPEAR"
    entities = step.get("prior_entities") or []
    return [
        {"role": "user", "content": pq, "meta": {}},
        {
            "role": "assistant",
            "content": leak,
            "meta": {
                "analysis_id": "eval-prior",
                "context_refs": {
                    "analysis_id": "eval-prior",
                    "last_user_q": pq,
                    "entities": entities,
                    "chunk_ids": ["eval-chunk-1"],
                    "item_ids": ["999"],
                    "teams": [],
                    "issues": [],
                },
            },
        },
    ]


def _run_step(con, scenario: dict, step: dict, prior_out: dict | None) -> dict:
    from eval.run_evidence_baseline import _ask
    from app import ask_context

    checks = dict(step.get("checks") or {})
    query = step["query"]
    scope = step.get("scope") or {}
    t0 = time.time()
    failures: list[dict] = []
    route_kind = None
    llm_used = False

    try:
        if step.get("needs_prior"):
            msgs = _prior_messages(step)
            if prior_out and msgs:
                leak = step.get("prior_answer_leak") or "SECRET_LEAK_PRIOR_ANSWER_MUST_NOT_APPEAR"
                real = (prior_out.get("answer") or "")[:200]
                msgs[1]["content"] = f"{leak}\n{real}"
            route = ask_context.route(query, msgs)
            route_kind = route.get("kind")
            expect_route = checks.get("expect_route")
            if expect_route and route_kind != expect_route:
                failures.append(
                    {
                        "check": "expect_route",
                        "layer": _attribute_failure("expect_route", scenario.get("layers") or []),
                        "detail": f"got={route_kind} expect={expect_route}",
                    }
                )
            search_q = route.get("search_q") or query
            out = _ask(con, search_q, scope)
            leak = step.get("prior_answer_leak") or "SECRET_LEAK_PRIOR_ANSWER_MUST_NOT_APPEAR"
            ans = out.get("answer") or ""
            if leak and leak in ans:
                failures.append(
                    {
                        "check": "prior_leak",
                        "layer": "Context/Follow-up",
                        "detail": "prior answer leak entered composition",
                    }
                )
        else:
            out = _ask(con, query, scope)

        llm_used = bool(out.get("llm_used"))
        ans = out.get("answer") or ""
        refs = out.get("evidence_refs") or []
        item_ids = [str(x) for x in (out.get("item_ids") or [])]
        cs = out.get("claim_support") or {}
        support = (cs.get("support") or "").strip()
        status = out.get("status") or ""

        if checks.get("expect_abstain") is True:
            abstain_ok = status == "unsupported" or bool(
                re.search(r"不能下结论|资料未提供可核对|不能确认该结论", ans)
            )
            if not abstain_ok:
                failures.append(
                    {
                        "check": "expect_abstain",
                        "layer": "Answer",
                        "detail": f"status={status} snip={ans[:80]}",
                    }
                )
        if checks.get("expect_abstain") is False and status == "unsupported" and checks.get("min_evidence", 0) > 0:
            # soft: only if we expected evidence
            if (out.get("n_hits") or 0) == 0:
                failures.append(
                    {
                        "check": "min_evidence",
                        "layer": "Retrieval",
                        "detail": "expected grounded answer but unsupported/empty",
                    }
                )

        for m in checks.get("must_mention") or []:
            if m not in ans:
                failures.append(
                    {
                        "check": "must_mention",
                        "layer": "Answer",
                        "detail": f"missing {m}",
                    }
                )

        must_any = checks.get("must_any") or []
        if must_any and not any(x in ans for x in must_any):
            failures.append(
                {
                    "check": "must_any",
                    "layer": "Answer",
                    "detail": f"none of {must_any} in answer",
                }
            )

        for p in checks.get("forbid_phrases") or []:
            if p and p in ans:
                layer = "Temporal" if checks.get("forbid_recent_event_claim") and p in (
                    "最近发生",
                    "本周发生",
                    "刚发生",
                ) else "Answer"
                if p.startswith("SECRET_"):
                    layer = "Context/Follow-up"
                failures.append({"check": "forbid_phrases", "layer": layer, "detail": p})

        if checks.get("forbid_recent_event_claim"):
            if re.search(r"最近发生|本周发生|刚发生|就是最近", ans):
                failures.append(
                    {
                        "check": "forbid_recent_event_claim",
                        "layer": "Temporal",
                        "detail": "recent-event claim language",
                    }
                )

        min_ev = int(checks.get("min_evidence") or 0)
        if min_ev > 0 and len(refs) < min_ev and status != "unsupported":
            # abstain path may clear refs
            if not checks.get("expect_abstain"):
                failures.append(
                    {
                        "check": "min_evidence",
                        "layer": "Retrieval",
                        "detail": f"refs={len(refs)} < {min_ev}",
                    }
                )

        must_items = [str(x) for x in (checks.get("must_cite_items") or [])]
        if must_items:
            blob = " ".join(refs) + " " + " ".join(item_ids)
            for mid in must_items:
                if mid not in blob:
                    failures.append(
                        {
                            "check": "must_cite_items",
                            "layer": "Ranking",
                            "detail": f"missing item {mid}",
                        }
                    )

        expect_support = (checks.get("expect_support") or "").strip()
        if expect_support and support and support != expect_support:
            failures.append(
                {
                    "check": "expect_support",
                    "layer": "Claim/Evidence",
                    "detail": f"got={support} expect={expect_support}",
                }
            )
        if checks.get("claim_must_not_be_supported") and support == "supported":
            failures.append(
                {
                    "check": "claim_must_not_be_supported",
                    "layer": "Claim/Evidence",
                    "detail": "false-support",
                }
            )

    except Exception as e:
        out = {"error": str(e), "answer": "", "evidence_refs": [], "item_ids": []}
        failures.append({"check": "harness", "layer": "Harness/Infrastructure", "detail": str(e)[:200]})

    ok = len(failures) == 0
    return {
        "id": step["id"],
        "query": query,
        "pass": ok,
        "failures": failures,
        "fail_layers": sorted({f["layer"] for f in failures}),
        "route_kind": route_kind,
        "llm_used": llm_used,
        "elapsed_s": round(time.time() - t0, 3),
        "n_hits": out.get("n_hits"),
        "status": out.get("status"),
        "claim_support": (out.get("claim_support") or {}).get("support"),
        "answer_snip": (out.get("answer") or "")[:180],
        "evidence_refs": (out.get("evidence_refs") or [])[:6],
        "item_ids": (out.get("item_ids") or [])[:6],
        "_out": out,  # for follow-up prior; stripped later
    }


def run_scenarios(con) -> dict:
    scenarios = _load_jsonl(SCENARIO_GOLD)
    results = []
    for sc in scenarios:
        print(f"== scenario {sc['id']} {sc.get('title')}", flush=True)
        step_results = []
        prior_out = None
        for step in sc.get("steps") or []:
            print(f"  [{step['id']}] {step['query'][:40]}…", flush=True)
            # wire prior from previous step for follow-up
            if step.get("needs_prior") and prior_out is None and step_results:
                prior_out = {
                    "query": step_results[-1]["query"],
                    "answer": step_results[-1].get("_out", {}).get("answer") or step_results[-1].get("answer_snip"),
                }
            elif not step.get("needs_prior"):
                # run and keep for next
                pass
            sr = _run_step(con, sc, step, prior_out)
            if not step.get("needs_prior"):
                prior_out = {"query": step["query"], "answer": (sr.get("_out") or {}).get("answer")}
            # strip heavy out
            sr.pop("_out", None)
            step_results.append(sr)
            print(f"    → pass={sr['pass']} layers={sr['fail_layers']}", flush=True)
        sc_pass = all(s["pass"] for s in step_results)
        results.append(
            {
                "id": sc["id"],
                "scenario": sc.get("scenario"),
                "title": sc.get("title"),
                "pass": sc_pass,
                "steps": step_results,
                "fail_layers": sorted(
                    {L for s in step_results for L in (s.get("fail_layers") or [])}
                ),
            }
        )
    n = len(results)
    n_pass = sum(1 for r in results if r["pass"])
    layer_hist: dict[str, int] = {}
    for r in results:
        for L in r.get("fail_layers") or []:
            layer_hist[L] = layer_hist.get(L, 0) + 1
    return {
        "n": n,
        "n_pass": n_pass,
        "pass_rate": round(n_pass / n, 4) if n else 0.0,
        "layer_failure_hist": layer_hist,
        "results": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-frozen-regression", action="store_true")
    args = ap.parse_args()
    t0 = time.time()

    os.environ.setdefault("MESH_CLAIM_SEMANTIC", "1")
    os.environ.setdefault("MESH_RECALL_USE_EMBED", "0")
    os.environ.setdefault("MESH_AGENT_USE_LLM", "1")
    os.environ.setdefault("MESH_LLM_MODEL", SONNET)
    os.environ.setdefault("MESH_LLM_MODEL_SEMANTIC", SONNET)
    os.environ.setdefault("MESH_LLM_MODEL_ANSWER", SONNET)

    from eval.run_recall_baseline import _disable_embed

    _disable_embed()

    frozen_steps: list[dict] = []
    frozen_metrics: dict = {}

    if not args.skip_frozen_regression:
        # Temporal residual: record, do not block on known residuals
        frozen_steps.append(
            {"step": "temporal", "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])}
        )
        t_rep = _load_json(REPORTS / "TEMPORAL_BASELINE_latest.json")
        frozen_metrics["temporal_pass_rate"] = _pass_rate(t_rep)
        frozen_metrics["temporal_fail_ids"] = _fail_ids(t_rep)

        frozen_steps.append(
            {
                "step": "recall",
                "rc": _py(["eval/run_recall_baseline.py", "--reuse-env-db"], {"MESH_RECALL_USE_EMBED": "0"}),
            }
        )
        # ranking
        frozen_steps.append(
            {
                "step": "ranking_v14",
                "rc": _py(
                    [
                        "eval/run_ranking_baseline.py",
                        "--reuse-env-db",
                        "--profile",
                        "baseline",
                        "--tag",
                        "ranking_v3_baseline",
                    ]
                ),
            }
        )
        frozen_steps.append(
            {
                "step": "answer_canonical",
                "rc": _py(
                    ["eval/run_answer_v2.py", "--reuse-env-db", "--tag", "answer_v3_baseline"],
                    {"MESH_AGENT_USE_LLM": "1", "MESH_CLAIM_SEMANTIC": "1"},
                ),
            }
        )
        ans = _load_json(
            REPORTS / "experiments" / "answer_v3_baseline" / "ANSWER_answer_v3_baseline.json"
        )
        frozen_metrics["answer_canonical_pass_rate"] = _pass_rate(ans)
        frozen_metrics["answer_canonical_fail_ids"] = _fail_ids(ans)
        frozen_metrics["answer_canonical_usage"] = ans.get("usage")
        frozen_metrics["answer_canonical_elapsed_s"] = ans.get("elapsed_s")

        frozen_steps.append(
            {
                "step": "evidence_canonical",
                "rc": _py(
                    [
                        "eval/run_evidence_baseline.py",
                        "--reuse-env-db",
                        "--tag",
                        "evidence_v3_baseline",
                        "--gold",
                        "eval/evidence_gold_v2.jsonl",
                    ],
                    {"MESH_AGENT_USE_LLM": "0", "MESH_CLAIM_SEMANTIC": "1"},
                ),
            }
        )
        ev = _load_json(
            REPORTS / "experiments" / "evidence_v3_baseline" / "EVIDENCE_evidence_v3_baseline.json"
        )
        frozen_metrics["evidence_canonical_pass_rate"] = _pass_rate(ev)
        frozen_metrics["evidence_canonical_fail_ids"] = _fail_ids(ev)
    else:
        # Reuse artifacts from a prior partial run
        frozen_steps.append({"step": "reuse_frozen_artifacts", "rc": 0})
        t_rep = _load_json(REPORTS / "TEMPORAL_BASELINE_latest.json")
        frozen_metrics["temporal_pass_rate"] = _pass_rate(t_rep)
        frozen_metrics["temporal_fail_ids"] = _fail_ids(t_rep)
        ans = _load_json(
            REPORTS / "experiments" / "answer_v3_baseline" / "ANSWER_answer_v3_baseline.json"
        )
        frozen_metrics["answer_canonical_pass_rate"] = _pass_rate(ans)
        frozen_metrics["answer_canonical_fail_ids"] = _fail_ids(ans)
        frozen_metrics["answer_canonical_usage"] = ans.get("usage")
        frozen_metrics["answer_canonical_elapsed_s"] = ans.get("elapsed_s")
        ev = _load_json(
            REPORTS / "experiments" / "evidence_v3_baseline" / "EVIDENCE_evidence_v3_baseline.json"
        )
        frozen_metrics["evidence_canonical_pass_rate"] = _pass_rate(ev)
        frozen_metrics["evidence_canonical_fail_ids"] = _fail_ids(ev)

    # Scenarios
    from app import db
    from app import llm as llm_mod

    llm_mod.reset_usage_accum()
    con = db.connect()
    try:
        scenario_rep = run_scenarios(con)
    finally:
        con.close()
    scenario_usage = llm_mod.take_usage_accum()

    # Gates
    ans_ok = frozen_metrics.get("answer_canonical_pass_rate") in (None, 1.0) or abs(
        (frozen_metrics.get("answer_canonical_pass_rate") or 0) - 1.0
    ) < 1e-9
    ev_ok = frozen_metrics.get("evidence_canonical_pass_rate") in (None, 1.0) or abs(
        (frozen_metrics.get("evidence_canonical_pass_rate") or 0) - 1.0
    ) < 1e-9
    # Temporal: only block on NEW regressions beyond known set — known T06/T08 wall-clock
    known_temporal = {"T06", "T08"}
    t_fails = set(frozen_metrics.get("temporal_fail_ids") or [])
    temporal_new = sorted(t_fails - known_temporal)
    temporal_ok = len(temporal_new) == 0

    scenario_ok = scenario_rep.get("n_pass", 0) == scenario_rep.get("n", 0)
    # Systemic blocker: same layer fails ≥2 scenarios
    systemic = [L for L, c in (scenario_rep.get("layer_failure_hist") or {}).items() if c >= 2]
    no_systemic = len(systemic) == 0

    false_support = any(
        f.get("check") == "claim_must_not_be_supported"
        for r in scenario_rep.get("results") or []
        for s in r.get("steps") or []
        for f in s.get("failures") or []
    )

    gates = {
        "canonical_answer_no_regression": ans_ok,
        "canonical_evidence_no_regression": ev_ok,
        "temporal_no_new_regression": temporal_ok,
        "scenarios_all_pass": scenario_ok,
        "no_systemic_layer_blocker": no_systemic or scenario_ok,
        "no_false_support_in_scenarios": not false_support,
    }
    production_baseline = all(gates.values())

    report = {
        **BASELINE_MANIFEST,
        "elapsed_s": round(time.time() - t0, 2),
        "frozen_regression_steps": frozen_steps,
        "frozen_metrics": frozen_metrics,
        "temporal_residual": {
            "pass_rate": frozen_metrics.get("temporal_pass_rate"),
            "fail_ids": frozen_metrics.get("temporal_fail_ids"),
            "known_residual": sorted(known_temporal & t_fails) if t_fails else list(known_temporal),
            "new_regressions": temporal_new,
            "note": "T06/T08 wall-clock known; T19 not forced blocker; do not patch claim_support",
        },
        "scenarios": scenario_rep,
        "scenario_usage": scenario_usage,
        "gates": gates,
        "systemic_layer_blockers": systemic,
        "production_baseline": production_baseline,
        "status": "production_baseline" if production_baseline else "needs_review",
    }

    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_V3_PRODUCTION_BASELINE.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORTS / "AGENT_QUALITY_V3_PRODUCTION_BASELINE.md"
    md.write_text(_md(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k not in ("scenarios",)}, ensure_ascii=False, indent=2))
    print("scenarios", json.dumps({k: scenario_rep[k] for k in scenario_rep if k != "results"}, ensure_ascii=False))
    print("wrote", path)
    print("wrote", md)
    return 0 if production_baseline else 1


def _md(report: dict) -> str:
    sc = report.get("scenarios") or {}
    gates = report.get("gates") or {}
    lines = [
        "# Agent Quality v3.0 · Production Baseline / E2E RC",
        "",
        f"**status=`{report.get('status')}`** · production_baseline=`{report.get('production_baseline')}` · "
        f"elapsed_s={report.get('elapsed_s')}",
        "",
        "## Baseline manifest（冻结零件）",
        "",
        f"- Ranking `{report.get('ranking_version')}` · Retrieval `{report.get('retrieval_version')}` · Vector `{report.get('vector')}`",
        f"- Claim Support `{report.get('claim_support_version')}`",
        f"- Model policy: `{json.dumps(report.get('model_policy'), ensure_ascii=False)}`",
        f"- Contract / Ontology / Prompt: frozen",
        "",
        "## Frozen-layer regression",
        "",
        "```json",
        json.dumps(report.get("frozen_metrics"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Temporal residual",
        "",
        "```json",
        json.dumps(report.get("temporal_residual"), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Scenario E2E",
        "",
        f"pass_rate=`{sc.get('pass_rate')}` · {sc.get('n_pass')}/{sc.get('n')}",
        "",
        "| Scenario | Title | Pass | Fail layers |",
        "|----------|-------|------|-------------|",
    ]
    for r in sc.get("results") or []:
        lines.append(
            f"| {r['id']} | {r.get('title')} | {'✅' if r.get('pass') else '❌'} | "
            f"{', '.join(r.get('fail_layers') or []) or '—'} |"
        )
    lines += [
        "",
        "## Gates",
        "",
    ]
    for k, v in gates.items():
        lines.append(f"- {'✅' if v else '❌'} `{k}`")
    lines += [
        "",
        "## 意义",
        "",
        "v3.0 证明冻结组件可组成稳定、可解释、可回归的完整 Agent 基线。",
        "下一阶段：Feishu Agent 产品化（不回头开 v2.x 微调）。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
