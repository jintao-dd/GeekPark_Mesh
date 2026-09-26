#!/usr/bin/env python3
"""Performance Baseline v1 · 健康单请求（非质量版本）。

固定环境：REPRO_STATUS=PASS · Vector OFF · Sonnet · Ranking v1.4 · Claim v2.4c-2
回答：一个正常问题到底要多少秒？三类问句各跑 N 轮算 P50/P95。

内部目标（不卡产品）：
  简单事实      P95 < 5s
  普通检索问答  P95 < 10s
  强 Claim      P95 < 15s

用法（tmesh 容器内）：
  MESH_AGENT_USE_LLM=1 python eval/run_perf_baseline_v1.py --reuse-env-db --rounds 5
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISSUE = "2026-8-17"
OPEN_ID = "ou_agent_perf_baseline"

# 三类：简单事实 / 普通检索 / 强 Claim
CASES = [
    {
        "kind": "simple_fact",
        "target_p95_ms": 5000,
        "query": "本期期号是什么",
    },
    {
        "kind": "normal_retrieval",
        "target_p95_ms": 10000,
        "query": "编辑部关注了哪些话题或公司",
    },
    {
        "kind": "strong_claim",
        "target_p95_ms": 15000,
        "query": "资料能否证明它已经量产？",
    },
]


def _pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    k = (len(ys) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(ys) - 1)
    if f == c:
        return ys[f]
    return ys[f] + (ys[c] - ys[f]) * (k - f)


def _summary(xs: list[float]) -> dict[str, Any]:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean_ms": round(statistics.mean(xs), 1),
        "p50_ms": round(_pct(xs, 50), 1),
        "p95_ms": round(_pct(xs, 95), 1),
        "max_ms": round(max(xs), 1),
    }


def _ensure_user(con) -> None:
    row = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (OPEN_ID,)
    ).fetchone()
    if row:
        return
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('agent_perf_baseline','Agent Perf Baseline','x','viewer','编辑部',?)",
        (OPEN_ID,),
    )
    con.commit()


def _gate_env() -> dict[str, Any]:
    from app import embeddings
    from app.repro_selfcheck import environment_manifest, last_status, run_selfcheck

    st = last_status() or run_selfcheck(probe_embed=True)
    man = environment_manifest(embedding_calls=embeddings.call_count())
    failures: list[str] = []
    if st.get("REPRO_STATUS") != "PASS":
        failures.append(f"REPRO_STATUS={st.get('REPRO_STATUS')}")
    if man.get("vector") != "OFF" or man.get("vector_enabled"):
        failures.append("vector_not_OFF")
    model = (man.get("model") or "").lower()
    if "sonnet" not in model:
        failures.append(f"model_not_sonnet:{man.get('model')}")
    if man.get("ranking") != "v1.4":
        failures.append(f"ranking={man.get('ranking')}")
    if not str(man.get("claim_support") or "").startswith("v2.4c-2"):
        failures.append(f"claim={man.get('claim_support')}")
    return {
        "REPRO_STATUS": st.get("REPRO_STATUS"),
        "environment_manifest": man,
        "gate_failures": failures,
        "gate": "PASS" if not failures else "FAIL",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--rounds", type=int, default=5)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--issue", default=ISSUE)
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    os.environ.setdefault("MESH_AGENT_USE_LLM", "1")

    gate = _gate_env()
    if gate["gate"] != "PASS":
        print(f"ENV_GATE_FAIL {gate['gate_failures']}", flush=True)
        return 2

    from app import db, embeddings

    # same-process import of profiler (eval/ on path via ROOT parent of this file)
    import importlib.util

    _prof_path = ROOT / "eval" / "run_agent_request_profile.py"
    _spec = importlib.util.spec_from_file_location("run_agent_request_profile", _prof_path)
    assert _spec and _spec.loader
    _prof = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_prof)
    profile_one = _prof.profile_one

    embeddings.reset_call_count()
    con = db.connect()
    report: dict[str, Any] = {
        "baseline_kind": "performance_baseline_v1",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "issue": args.issue,
        "rounds": args.rounds,
        "targets_ms": {c["kind"]: c["target_p95_ms"] for c in CASES},
        "REPRO_STATUS": gate["REPRO_STATUS"],
        "environment_manifest": gate["environment_manifest"],
        "cases": [],
    }
    try:
        _ensure_user(con)
        # warmup once on normal retrieval
        for _ in range(max(0, args.warmup)):
            profile_one(con, CASES[1]["query"], issue=args.issue, use_llm=True)

        for case in CASES:
            totals: list[float] = []
            embeds: list[int] = []
            answer_ms: list[float] = []
            semantic_ms: list[float] = []
            dominant: list[str] = []
            print(f"==> {case['kind']} q={case['query']!r} rounds={args.rounds}", flush=True)
            for i in range(args.rounds):
                embeddings.reset_call_count()
                run = profile_one(con, case["query"], issue=args.issue, use_llm=True)
                s = run["summary"]
                totals.append(float(s["total_profiled_tool_ms"]))
                embeds.append(int(s["embed_call_count"]))
                answer_ms.append(float(s["answer_ms_sum"] or 0))
                semantic_ms.append(float(s["semantic_ms_sum"] or 0))
                dominant.append(str(s.get("dominant") or ""))
                print(
                    f"   r{i+1} total={s['total_profiled_tool_ms']}ms "
                    f"embed={s['embed_call_count']} ans={s['answer_ms_sum']} "
                    f"sem={s['semantic_ms_sum']} dom={s['dominant']}",
                    flush=True,
                )
            tot_s = _summary(totals)
            row = {
                "kind": case["kind"],
                "query": case["query"],
                "target_p95_ms": case["target_p95_ms"],
                "total_ms": tot_s,
                "answer_ms": _summary(answer_ms),
                "semantic_ms": _summary(semantic_ms),
                "embed_call_count_max": max(embeds) if embeds else 0,
                "embed_call_count_sum": sum(embeds),
                "dominant_mode": max(set(dominant), key=dominant.count) if dominant else "",
                "meets_internal_target": bool(
                    tot_s.get("n") and float(tot_s.get("p95_ms") or 0) <= case["target_p95_ms"]
                ),
            }
            report["cases"].append(row)
    finally:
        con.close()

    embed_total = sum(int(c.get("embed_call_count_sum") or 0) for c in report["cases"])
    man = dict(report["environment_manifest"] or {})
    man["embedding_calls"] = embed_total
    report["environment_manifest"] = man
    if embed_total > 0:
        report["environment_gate"] = "FAIL"
        print(f"ENVIRONMENT_GATE_FAIL embedding_calls={embed_total}", flush=True)
    else:
        report["environment_gate"] = "PASS"

    out_json = Path(
        args.out_json
        or str(ROOT / "eval" / "reports" / "AGENT_PERF_BASELINE_V1.tmesh.json")
    )
    out_md = Path(
        args.out_md
        or str(ROOT / "eval" / "reports" / "AGENT_PERF_BASELINE_V1.tmesh.md")
    )
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Performance Baseline v1 · 健康单请求",
        "",
        f"**when**=`{report['timestamp']}` · **issue**=`{args.issue}` · **rounds**=`{args.rounds}`",
        "",
        "## Environment Manifest",
        "",
        f"- commit=`{man.get('commit')}`",
        f"- image=`{man.get('image_digest') or man.get('image_tag')}`",
        f"- model=`{man.get('model')}`",
        f"- vector=`{man.get('vector')}`",
        f"- embedding_calls=`{man.get('embedding_calls')}`",
        f"- ranking=`{man.get('ranking')}`",
        f"- claim_support=`{man.get('claim_support')}`",
        f"- REPRO_STATUS=`{report.get('REPRO_STATUS')}`",
        f"- environment_gate=`{report.get('environment_gate')}`",
        "",
        "## Cases (internal targets, not product gates)",
        "",
        "| kind | query | P50 | P95 | target P95 | answer P95 | semantic P95 | embed | meet? |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for c in report["cases"]:
        t = c.get("total_ms") or {}
        a = c.get("answer_ms") or {}
        s = c.get("semantic_ms") or {}
        lines.append(
            "| {kind} | {q} | {p50} | {p95} | {tgt} | {ap} | {sp} | {emb} | {ok} |".format(
                kind=c["kind"],
                q=(c.get("query") or "")[:24],
                p50=t.get("p50_ms"),
                p95=t.get("p95_ms"),
                tgt=c.get("target_p95_ms"),
                ap=a.get("p95_ms"),
                sp=s.get("p95_ms"),
                emb=c.get("embed_call_count_max"),
                ok="YES" if c.get("meets_internal_target") else "NO",
            )
        )
    lines += [
        "",
        "## Verdict",
        "",
        "单请求主导耗时看 answer_ms / semantic_ms；embedding_calls 必须为 0。",
        "若普通检索仍 ~10–14s 且 answer 占绝大多数 → 下一刀查 prompt/evidence/context/completion token，不改 RAG。",
        "",
    ]
    out_md.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {out_json}", flush=True)
    print(f"wrote {out_md}", flush=True)
    for c in report["cases"]:
        t = c.get("total_ms") or {}
        print(
            f"CASE {c['kind']} p95={t.get('p95_ms')} target={c['target_p95_ms']} "
            f"meet={c['meets_internal_target']} embed_max={c['embed_call_count_max']}",
            flush=True,
        )
    return 0 if report["environment_gate"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
