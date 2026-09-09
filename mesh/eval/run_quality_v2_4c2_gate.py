#!/usr/bin/env python3
"""Agent Quality v2.4c-2 · Claim Semantic Extension Gate（生产接入验收）。

MESH_CLAIM_SEMANTIC=1（默认）正式写入 assess_claim_support。
一次性：Temporal + Recall + Ranking + Canonical Answer/Evidence + Unseen。

硬门：canonical 不回退、false-support 消除、E28 不误杀、worse=[]。
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


def _py(args: list[str], env: dict | None = None) -> int:
    e = os.environ.copy()
    e["PYTHONPATH"] = str(ROOT)
    e.setdefault("MESH_CLAIM_SEMANTIC", "1")
    e.setdefault("MESH_RECALL_USE_EMBED", "0")
    if env:
        e.update(env)
    print(">>", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _find_latest(glob_pat: str) -> Path | None:
    hits = sorted(REPORTS.glob(glob_pat), key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-temporal", action="store_true")
    ap.add_argument("--skip-recall", action="store_true")
    ap.add_argument("--skip-ranking", action="store_true")
    args = ap.parse_args()
    t0 = time.time()
    os.environ["MESH_CLAIM_SEMANTIC"] = "1"
    steps: list[dict] = []

    if not args.skip_temporal:
        steps.append({"step": "temporal", "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])})
    if not args.skip_recall:
        steps.append(
            {
                "step": "recall",
                "rc": _py(["eval/run_recall_baseline.py", "--reuse-env-db"], {"MESH_RECALL_USE_EMBED": "0"}),
            }
        )
    if not args.skip_ranking:
        steps.append(
            {
                "step": "ranking_prod_v14",
                "rc": _py(
                    [
                        "eval/run_ranking_baseline.py",
                        "--reuse-env-db",
                        "--profile",
                        "baseline",
                        "--tag",
                        "ranking_gate_v24c2",
                    ]
                ),
            }
        )

    steps.append(
        {
            "step": "answer_canonical",
            "rc": _py(
                ["eval/run_answer_v2.py", "--reuse-env-db", "--tag", "answer_gate_v24c2"],
                {"MESH_CLAIM_SEMANTIC": "1", "MESH_AGENT_USE_LLM": "0"},
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
                    "evidence_gate_v24c2",
                    "--gold",
                    "eval/evidence_gold_v2.jsonl",
                ],
                {"MESH_CLAIM_SEMANTIC": "1", "MESH_AGENT_USE_LLM": "0"},
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
                    "answer_unseen_gate_v24c2",
                    "--gold",
                    "eval/answer_gold_unseen_v23e.jsonl",
                ],
                {"MESH_CLAIM_SEMANTIC": "1", "MESH_AGENT_USE_LLM": "0"},
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
                    "evidence_unseen_gate_v24c2",
                    "--gold",
                    "eval/evidence_gold_unseen_v23e.jsonl",
                ],
                {"MESH_CLAIM_SEMANTIC": "1", "MESH_AGENT_USE_LLM": "0"},
            ),
        }
    )

    # collect metrics (written under eval/reports/experiments/{tag}/)
    ans_c = _load(REPORTS / "experiments" / "answer_gate_v24c2" / "ANSWER_answer_gate_v24c2.json")
    ev_c = _load(REPORTS / "experiments" / "evidence_gate_v24c2" / "EVIDENCE_evidence_gate_v24c2.json")
    if not ev_c:
        ev_c = _load(REPORTS / "EVIDENCE_evidence_gate_v24c2.json")
    ans_u = _load(
        REPORTS / "experiments" / "answer_unseen_gate_v24c2" / "ANSWER_answer_unseen_gate_v24c2.json"
    )
    ev_u = _load(
        REPORTS / "experiments" / "evidence_unseen_gate_v24c2" / "EVIDENCE_evidence_unseen_gate_v24c2.json"
    )
    if not ev_u:
        ev_u = _load(REPORTS / "EVIDENCE_evidence_unseen_gate_v24c2.json")

    def _pass_rate(rep: dict) -> float | None:
        if not rep:
            return None
        if "pass_rate" in rep:
            return float(rep["pass_rate"])
        s = rep.get("summary") or {}
        if "pass_rate" in s:
            return float(s["pass_rate"])
        n = s.get("n") or rep.get("n")
        p = s.get("n_pass") or s.get("passed")
        if n and p is not None:
            return float(p) / float(n)
        results = rep.get("results") or rep.get("cases") or []
        if results:
            ok = sum(1 for r in results if r.get("pass") or (r.get("metrics") or {}).get("pass"))
            return ok / len(results)
        return None

    def _failed_ids(rep: dict) -> list[str]:
        out = []
        for r in rep.get("results") or rep.get("cases") or []:
            ok = r.get("pass")
            if ok is None:
                ok = (r.get("metrics") or {}).get("pass")
            if not ok:
                out.append(r.get("id") or "?")
        return out

    ans_c_rate = _pass_rate(ans_c)
    ev_c_rate = _pass_rate(ev_c)
    ans_u_rate = _pass_rate(ans_u)
    ev_u_rate = _pass_rate(ev_u)
    ans_u_fail = _failed_ids(ans_u)
    ev_u_fail = _failed_ids(ev_u)

    # hard gates
    gates = {
        "canonical_answer_full": ans_c_rate is not None and abs(ans_c_rate - 1.0) < 1e-9,
        "canonical_evidence_full": ev_c_rate is not None and abs(ev_c_rate - 1.0) < 1e-9,
        "unseen_answer_cleared": ans_u_rate is not None and ans_u_rate >= 0.99,
        "unseen_evidence_cleared": ev_u_rate is not None and ev_u_rate >= 0.99,
        "E28_ok": "E28" not in ev_u_fail,
        "steps_ok": all(s["rc"] == 0 for s in steps if s["step"] in ("answer_canonical", "evidence_canonical")),
        "worse_empty": True,  # vs v2.3e canonical=1.0; any fail → worse
    }
    # worse: canonical regression
    worse = []
    if not gates["canonical_answer_full"]:
        worse += [f"answer:{i}" for i in _failed_ids(ans_c)]
    if not gates["canonical_evidence_full"]:
        worse += [f"evidence:{i}" for i in _failed_ids(ev_c)]
    gates["worse_empty"] = len(worse) == 0
    gate_ok = all(gates.values()) and all(
        s["rc"] == 0 for s in steps if s["step"] not in ("temporal",)  # temporal residual T19 allowed
    )
    # Temporal: don't fail gate on T19 alone — record score
    temporal_rep = _load(REPORTS / "TEMPORAL_BASELINE.json")
    if not temporal_rep:
        p = _find_latest("TEMPORAL*.json") or _find_latest("*temporal*.json")
        temporal_rep = _load(p) if p else {}

    report = {
        "phase": "quality_v2_4c2_claim_semantic_gate",
        "elapsed_s": round(time.time() - t0, 2),
        "baseline": {
            "mesh_claim_semantic": "1",
            "mesh_agent_use_llm": "0",
            "claim_support": "v2.4c2_semantic_extension",
            "ranking": "v1.4",
            "vector": "OFF",
        },
        "steps": steps,
        "metrics": {
            "answer_canonical_pass_rate": ans_c_rate,
            "evidence_canonical_pass_rate": ev_c_rate,
            "answer_unseen_pass_rate": ans_u_rate,
            "evidence_unseen_pass_rate": ev_u_rate,
            "answer_unseen_fail_ids": ans_u_fail,
            "evidence_unseen_fail_ids": ev_u_fail,
            "temporal": temporal_rep.get("summary") or temporal_rep.get("pass_rate") or temporal_rep,
        },
        "hard_gates": gates,
        "worse_ids": worse,
        "gate_ok": gate_ok,
        "freeze_claim_support": gate_ok,
    }

    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_V2_4C2_CLAIM_SEMANTIC_GATE.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORTS / "AGENT_QUALITY_V2_4C2_CLAIM_SEMANTIC_GATE.md"
    md.write_text(_md(report), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", md)
    return 0 if gate_ok else 1


def _md(report: dict) -> str:
    g = report["hard_gates"]
    lines = [
        "# Agent Quality v2.4c-2 · Claim Semantic Extension Gate",
        "",
        f"**gate_ok = `{report['gate_ok']}`** · freeze_claim_support=`{report['freeze_claim_support']}` · "
        f"worse=`{report['worse_ids']}`",
        "",
        "## Hard gates",
        "",
    ]
    for k, v in g.items():
        lines.append(f"- {'✅' if v else '❌'} `{k}`")
    lines += [
        "",
        "## Metrics",
        "",
        "```json",
        json.dumps(report["metrics"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Steps",
        "",
        "```json",
        json.dumps(report["steps"], ensure_ascii=False, indent=2),
        "```",
        "",
        "## Next",
        "",
        "- gate_ok → Claim Support **frozen**；立即 Model A/B",
        "- 否则修 semantic extension（仍不堆关键词），不新开 shadow 版本号游戏",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
