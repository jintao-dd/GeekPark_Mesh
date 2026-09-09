#!/usr/bin/env python3
"""任务级模型配置 A/B（非动态 router）。

固定：Ranking / Recall / Claim Support v2.4c-2 / Contract / Prompt / Gold。
只换任务→模型环境变量：
  MESH_LLM_MODEL_SEMANTIC  — Claim semantic judge
  MESH_LLM_MODEL_ANSWER    — Answer 成文

配置表（至少）：
  opus_all              semantic=Opus  answer=Opus
  sonnet_all            semantic=Sonnet answer=Sonnet
  sonnet_sem_opus_ans   semantic=Sonnet answer=Opus（敏感成文）

记录 quality / latency / token / cost(估价)。
Temporal residual 单独记录，不改 Claim Support。

用法：
  PYTHONPATH=. python eval/run_quality_model_task_ab.py --reuse-env-db
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"

OPUS = "anthropic/claude-4.8-opus"
SONNET = "claude-sonnet-4-6"

# MODELINK 公开标价（USD / 1M tokens）；无不到的记 null
# Opus：页面给出 output=$25、cache_input=$0.5；input 未单独标时用 cache_input 作低估近似并标注
RATE_USD_PER_M = {
    OPUS: {"input": 0.5, "output": 25.0, "note": "modelink cache_input≈input proxy; output=$25/M"},
    SONNET: {"input": None, "output": None, "note": "modelink rate not locked in this run"},
    "claude-haiku-4-5": {"input": None, "output": None, "note": "rate unset"},
    "openai/gpt-5.4-mini": {"input": 0.75, "output": 4.5, "note": "modelink card"},
    "openai/gpt-5.4-nano": {"input": 0.2, "output": 1.25, "note": "modelink card"},
    "gpt-5.4-mini": {"input": 0.75, "output": 4.5, "note": "modelink card"},
}

CONFIGS = [
    {
        "id": "opus_all",
        "label": "全 Opus（对照）",
        "semantic": OPUS,
        "answer": OPUS,
        "role": "baseline",
    },
    {
        "id": "sonnet_all",
        "label": "全 Sonnet（默认候选）",
        "semantic": SONNET,
        "answer": SONNET,
        "role": "default_candidate",
    },
    {
        "id": "sonnet_sem_opus_ans",
        "label": "semantic=Sonnet / answer=Opus（敏感成文）",
        "semantic": SONNET,
        "answer": OPUS,
        "role": "sensitive_compose",
    },
]


def _py(args: list[str], env: dict) -> tuple[int, float]:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT)
    e.update(env)
    print(">>", " ".join(args), flush=True)
    t0 = time.time()
    rc = subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)
    return rc, round(time.time() - t0, 2)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _estimate_cost(usage: dict, models_used: list[str]) -> dict:
    """粗估：若多模型混用，按 usage 总量套「主成文模型」费率并标注 approximate。"""
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    # 优先 answer 模型费率（成文通常占 completion）
    rate = None
    rate_model = None
    for m in models_used:
        if m in RATE_USD_PER_M and RATE_USD_PER_M[m].get("output") is not None:
            rate = RATE_USD_PER_M[m]
            rate_model = m
            break
    if not rate or rate.get("input") is None or rate.get("output") is None:
        return {
            "usd_estimate": None,
            "rate_model": rate_model,
            "note": "rate incomplete; tokens recorded only",
        }
    usd = (pt / 1e6) * float(rate["input"]) + (ct / 1e6) * float(rate["output"])
    return {
        "usd_estimate": round(usd, 6),
        "rate_model": rate_model,
        "rate": rate,
        "note": rate.get("note"),
    }


def _run_config(cfg: dict) -> dict:
    tag = cfg["id"]
    env = {
        "MESH_CLAIM_SEMANTIC": "1",
        "MESH_AGENT_USE_LLM": "1",
        "MESH_RECALL_USE_EMBED": "0",
        "MESH_LLM_MODEL": cfg["answer"],  # default fallback
        "MESH_LLM_MODEL_SEMANTIC": cfg["semantic"],
        "MESH_LLM_MODEL_ANSWER": cfg["answer"],
    }
    steps = []
    wall0 = time.time()

    for step, args in (
        (
            "answer_canonical",
            ["eval/run_answer_v2.py", "--reuse-env-db", "--tag", f"taskab_{tag}_ans_c"],
        ),
        (
            "evidence_canonical",
            [
                "eval/run_evidence_baseline.py",
                "--reuse-env-db",
                "--tag",
                f"taskab_{tag}_ev_c",
                "--gold",
                "eval/evidence_gold_v2.jsonl",
            ],
        ),
        (
            "answer_unseen",
            [
                "eval/run_answer_v2.py",
                "--reuse-env-db",
                "--tag",
                f"taskab_{tag}_ans_u",
                "--gold",
                "eval/answer_gold_unseen_v23e.jsonl",
            ],
        ),
        (
            "evidence_unseen",
            [
                "eval/run_evidence_baseline.py",
                "--reuse-env-db",
                "--tag",
                f"taskab_{tag}_ev_u",
                "--gold",
                "eval/evidence_gold_unseen_v23e.jsonl",
            ],
        ),
    ):
        rc, elapsed = _py(args, env)
        steps.append({"step": step, "rc": rc, "elapsed_s": elapsed})

    def _rep(kind: str, suf: str) -> dict:
        if kind == "ans":
            p = REPORTS / "experiments" / f"taskab_{tag}_{suf}" / f"ANSWER_taskab_{tag}_{suf}.json"
        else:
            p = REPORTS / "experiments" / f"taskab_{tag}_{suf}" / f"EVIDENCE_taskab_{tag}_{suf}.json"
        return _load(p)

    ans_c = _rep("ans", "ans_c")
    ev_c = _rep("ev", "ev_c")
    ans_u = _rep("ans", "ans_u")
    ev_u = _rep("ev", "ev_u")

    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0}
    for r in (ans_c, ev_c, ans_u, ev_u):
        u = r.get("usage") or {}
        for k in ("prompt_tokens", "completion_tokens", "total_tokens", "n_calls"):
            usage[k] = int(usage.get(k) or 0) + int(u.get(k) or 0)

    def _pr(r: dict) -> float | None:
        return float(r["pass_rate"]) if r and "pass_rate" in r else None

    def _llm_n(r: dict) -> int:
        return sum(1 for x in (r.get("results") or []) if x.get("llm_used"))

    def _fails(r: dict) -> list[str]:
        return [
            x["id"]
            for x in (r.get("results") or [])
            if not (x.get("metrics") or {}).get("pass")
        ]

    metrics = {
        "answer_canonical": _pr(ans_c),
        "evidence_canonical": _pr(ev_c),
        "answer_unseen": _pr(ans_u),
        "evidence_unseen": _pr(ev_u),
        "answer_canonical_elapsed_s": ans_c.get("elapsed_s"),
        "evidence_canonical_elapsed_s": ev_c.get("elapsed_s"),
        "answer_unseen_elapsed_s": ans_u.get("elapsed_s"),
        "evidence_unseen_elapsed_s": ev_u.get("elapsed_s"),
        "answer_canonical_llm_used": _llm_n(ans_c),
        "answer_unseen_llm_used": _llm_n(ans_u),
        "fail_ids": {
            "answer_canonical": _fails(ans_c),
            "evidence_canonical": _fails(ev_c),
            "answer_unseen": _fails(ans_u),
            "evidence_unseen": _fails(ev_u),
        },
        "usage": usage,
    }
    quality_ok = all(
        metrics[k] is not None and abs(metrics[k] - 1.0) < 1e-9
        for k in ("answer_canonical", "evidence_canonical", "answer_unseen", "evidence_unseen")
    )
    cost = _estimate_cost(usage, [cfg["answer"], cfg["semantic"]])
    return {
        "config": cfg,
        "env": env,
        "steps": steps,
        "wall_s": round(time.time() - wall0, 2),
        "metrics": metrics,
        "quality_ok": quality_ok,
        "cost": cost,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--configs", default="", help="comma ids; default all")
    args = ap.parse_args()
    t0 = time.time()

    wanted = {x.strip() for x in args.configs.split(",") if x.strip()} or {c["id"] for c in CONFIGS}
    results = []
    for cfg in CONFIGS:
        if cfg["id"] not in wanted:
            continue
        print("===", cfg["id"], cfg["label"], flush=True)
        results.append(_run_config(cfg))

    # Temporal residual (single snapshot; do not touch claim_support)
    from app import llm as llm_mod

    os.environ["MESH_CLAIM_SEMANTIC"] = "1"
    rc_t, elapsed_t = _py(
        ["eval/run_temporal_baseline.py", "--reuse-env-db"],
        {
            "MESH_CLAIM_SEMANTIC": "1",
            "MESH_AGENT_USE_LLM": "0",
            "MESH_RECALL_USE_EMBED": "0",
            "MESH_LLM_MODEL": SONNET,
        },
    )
    t_rep = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    if not t_rep:
        t_rep = _load(REPORTS / "baselines" / "TEMPORAL_BASELINE_v1.json")

    recommended = None
    for r in results:
        if r["config"]["id"] == "sonnet_all" and r.get("quality_ok"):
            recommended = {
                "Claim semantic judge": SONNET,
                "Answer compose (default)": SONNET,
                "Answer compose (sensitive/external)": OPUS,
                "Evidence label": "claim_support v2.4c-2 (frozen; model-light)",
                "Temporal": "residual; do not patch claim_support",
            }
            break
    if not recommended:
        # fallback: first quality_ok
        for r in results:
            if r.get("quality_ok"):
                recommended = {
                    "Claim semantic judge": r["config"]["semantic"],
                    "Answer compose (default)": r["config"]["answer"],
                    "Answer compose (sensitive/external)": OPUS,
                }
                break

    report = {
        "phase": "quality_model_task_ab",
        "frozen_layers": {
            "retrieval_ranking": "frozen",
            "claim_support": "v2.4c-2_frozen_no_patch",
            "agent_contract": "frozen",
            "ontology": "frozen_doc_only",
            "gold": "unchanged",
            "dynamic_router": "forbidden",
        },
        "elapsed_s": round(time.time() - t0, 2),
        "configs": results,
        "temporal_residual": {
            "pass_rate": t_rep.get("pass_rate"),
            "n_fail": t_rep.get("n_fail"),
            "rc": rc_t,
            "elapsed_s": elapsed_t,
            "note": "Independent residual; do not modify Claim Support to chase 1.0",
        },
        "task_model_table": recommended,
        "status": "frozen" if recommended else "needs_review",
    }

    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_MODEL_TASK_AB.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    # freeze into QUALITY_MODEL_AB as the decision artifact
    ab_path = out_dir / "QUALITY_MODEL_AB.json"
    ab_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORTS / "AGENT_QUALITY_MODEL_TASK_AB.md"
    md.write_text(_md(report), encoding="utf-8")
    (REPORTS / "AGENT_QUALITY_MODEL_AB.md").write_text(_md(report), encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "configs"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", md)
    return 0 if report["status"] == "frozen" else 1


def _md(report: dict) -> str:
    lines = [
        "# Agent Quality · 任务级模型配置（冻结）",
        "",
        "> Claim Support **v2.4c-2 frozen** · 无动态 router · Gold/Contract/Ranking 不变",
        "",
        f"status=`{report.get('status')}` · elapsed_s={report.get('elapsed_s')}",
        "",
        "## 任务 → 模型（固定配置表）",
        "",
    ]
    table = report.get("task_model_table") or {}
    if table:
        lines += ["| 任务 | 模型 |", "|------|------|"]
        for k, v in table.items():
            lines.append(f"| {k} | `{v}` |")
    else:
        lines.append("_未形成推荐表（有配置未 quality_ok）_")
    lines += ["", "## 配置对比", ""]
    for r in report.get("configs") or []:
        c = r["config"]
        m = r["metrics"]
        lines += [
            f"### `{c['id']}` — {c['label']}",
            "",
            f"- semantic=`{c['semantic']}` · answer=`{c['answer']}`",
            f"- quality_ok=`{r.get('quality_ok')}` · wall_s={r.get('wall_s')}",
            f"- Answer C/U: {m.get('answer_canonical')} / {m.get('answer_unseen')} "
            f"(elapsed {m.get('answer_canonical_elapsed_s')}s / {m.get('answer_unseen_elapsed_s')}s, "
            f"llm_used {m.get('answer_canonical_llm_used')} / {m.get('answer_unseen_llm_used')})",
            f"- Evidence C/U: {m.get('evidence_canonical')} / {m.get('evidence_unseen')}",
            f"- tokens: `{m.get('usage')}` · cost≈`{(r.get('cost') or {}).get('usd_estimate')}` "
            f"({(r.get('cost') or {}).get('note')})",
            f"- fails: `{m.get('fail_ids')}`",
            "",
        ]
    tr = report.get("temporal_residual") or {}
    lines += [
        "## Temporal residual（单列）",
        "",
        f"- pass_rate=`{tr.get('pass_rate')}` · n_fail=`{tr.get('n_fail')}`",
        "- **禁止**为追 Temporal 1.0 修改已冻结 Claim Support",
        "",
        "## 下一步",
        "",
        "进入 Agent / Tool 设计；用本 frozen Gold / Contract / Claim Support 验收 Agent 是否真提升能力。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
