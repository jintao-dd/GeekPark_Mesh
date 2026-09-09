#!/usr/bin/env python3
"""Model A/B · 固定规则/Prompt/Ranking/semantic，只换 MESH_LLM_MODEL。

Model A = 当前基线 anthropic/claude-4.8-opus
Model B = MESH_LLM_MODEL_B 或探测到的较小模型；若无可用则 blocked。

用法：
  MESH_CLAIM_SEMANTIC=1 PYTHONPATH=. python eval/run_quality_model_ab.py --reuse-env-db
  MESH_LLM_MODEL_B=anthropic/claude-sonnet-4-6 ...  # 可选强制 B
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

MODEL_A_DEFAULT = "anthropic/claude-4.8-opus"
B_CANDIDATES = [
    "claude-sonnet-4-6",
    "anthropic/claude-sonnet-4-6",
    "anthropic/claude-4-sonnet",
]


def _py(args: list[str], env: dict) -> int:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT)
    e.update(env)
    print(">>", " ".join(args), {k: env[k] for k in env if k.startswith("MESH_")}, flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _probe_b(model_a: str, forced: str) -> str | None:
    if forced:
        return forced
    from app.providers import get_provider

    for m in B_CANDIDATES:
        if m == model_a:
            continue
        os.environ["MESH_LLM_MODEL"] = m
        try:
            p = get_provider()
            t = p.complete("只回复 ok", "ping", max_tokens=8)
            if t is not None:
                os.environ["MESH_LLM_MODEL"] = model_a
                return m
        except Exception:
            continue
    os.environ["MESH_LLM_MODEL"] = model_a
    return None


def _run_suite(tag: str, model: str) -> dict:
    env = {
        "MESH_LLM_MODEL": model,
        "MESH_CLAIM_SEMANTIC": "1",
        "MESH_AGENT_USE_LLM": "1",
        "MESH_RECALL_USE_EMBED": "0",
    }
    steps = []
    steps.append(
        {
            "step": "temporal",
            "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"], env),
        }
    )
    steps.append(
        {
            "step": "answer_canonical",
            "rc": _py(
                ["eval/run_answer_v2.py", "--reuse-env-db", "--tag", f"ab_{tag}_ans_c"],
                env,
            ),
        }
    )
    steps.append(
        {
            "step": "evidence_canonical",
            "rc": _py(
                [
                    "eval/run_evidence_baseline.py",
                    "--reuse-env-db",
                    "--tag",
                    f"ab_{tag}_ev_c",
                    "--gold",
                    "eval/evidence_gold_v2.jsonl",
                ],
                env,
            ),
        }
    )
    steps.append(
        {
            "step": "answer_unseen",
            "rc": _py(
                [
                    "eval/run_answer_v2.py",
                    "--reuse-env-db",
                    "--tag",
                    f"ab_{tag}_ans_u",
                    "--gold",
                    "eval/answer_gold_unseen_v23e.jsonl",
                ],
                env,
            ),
        }
    )
    steps.append(
        {
            "step": "evidence_unseen",
            "rc": _py(
                [
                    "eval/run_evidence_baseline.py",
                    "--reuse-env-db",
                    "--tag",
                    f"ab_{tag}_ev_u",
                    "--gold",
                    "eval/evidence_gold_unseen_v23e.jsonl",
                ],
                env,
            ),
        }
    )

    def pr(path: Path) -> float | None:
        rep = _load(path)
        return float(rep["pass_rate"]) if rep and "pass_rate" in rep else None

    metrics = {
        "answer_canonical": pr(REPORTS / "experiments" / f"ab_{tag}_ans_c" / f"ANSWER_ab_{tag}_ans_c.json"),
        "evidence_canonical": pr(REPORTS / "experiments" / f"ab_{tag}_ev_c" / f"EVIDENCE_ab_{tag}_ev_c.json"),
        "answer_unseen": pr(REPORTS / "experiments" / f"ab_{tag}_ans_u" / f"ANSWER_ab_{tag}_ans_u.json"),
        "evidence_unseen": pr(REPORTS / "experiments" / f"ab_{tag}_ev_u" / f"EVIDENCE_ab_{tag}_ev_u.json"),
    }
    t_rep = _load(REPORTS / "TEMPORAL_BASELINE.json")
    metrics["temporal"] = t_rep.get("pass_rate") if t_rep else None
    return {"model": model, "steps": steps, "metrics": metrics, "env": env}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--model-b", default=os.environ.get("MESH_LLM_MODEL_B", ""))
    ap.add_argument("--skip-b-if-missing", action="store_true", default=True)
    args = ap.parse_args()
    t0 = time.time()

    model_a = os.environ.get("MESH_LLM_MODEL") or MODEL_A_DEFAULT
    os.environ["MESH_LLM_MODEL"] = model_a
    model_b = _probe_b(model_a, (args.model_b or "").strip())

    report: dict = {
        "phase": "quality_model_ab",
        "elapsed_s": None,
        "fixed": {
            "ranking": "v1.4",
            "recall": "frozen",
            "vector": "OFF",
            "claim_semantic": "v2.4c2_frozen",
            "contract": "frozen",
            "prompt": "unchanged",
            "gold": "unchanged",
        },
        "model_a": model_a,
        "model_b": model_b,
        "status": "ok",
    }

    if not model_b:
        report["status"] = "ab_blocked"
        report["blocked_reason"] = (
            "当前环境仅配置/探测到 Model A；无第二可用较小模型。"
            "未虚构模型。设置 MESH_LLM_MODEL_B 后可重跑。"
        )
        report["A"] = None
        report["B"] = None
    else:
        print("=== Model A", model_a, flush=True)
        report["A"] = _run_suite("A", model_a)
        print("=== Model B", model_b, flush=True)
        report["B"] = _run_suite("B", model_b)
        # restore
        os.environ["MESH_LLM_MODEL"] = model_a

    report["elapsed_s"] = round(time.time() - t0, 2)
    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_MODEL_AB.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORTS / "AGENT_QUALITY_MODEL_AB.md"
    md.write_text(_md(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", md)
    return 0 if report["status"] != "error" else 1


def _md(report: dict) -> str:
    lines = [
        "# Agent Quality · Model A/B",
        "",
        f"status=`{report['status']}` · A=`{report.get('model_a')}` · B=`{report.get('model_b')}`",
        "",
        "固定：Ranking v1.4 · Recall · semantic v2.4c2 · Contract · Prompt · Gold",
        "",
    ]
    if report["status"] == "ab_blocked":
        lines += ["## Blocked", "", report.get("blocked_reason") or "", ""]
        return "\n".join(lines) + "\n"
    lines += ["## Metrics", ""]
    for side in ("A", "B"):
        block = report.get(side) or {}
        lines += [f"### Model {side}: `{block.get('model')}`", "", "```json"]
        lines.append(json.dumps(block.get("metrics"), ensure_ascii=False, indent=2))
        lines += ["```", ""]
    lines += [
        "## Decision hint",
        "",
        "- 若 A/B Answer/Evidence/Temporal 差距很小 → 可考虑任务级分层（非动态 router）",
        "- 若 B 在 Unseen/Temporal 明显掉点 → 关键任务保留 Opus",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
