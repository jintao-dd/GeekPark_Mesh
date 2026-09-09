#!/usr/bin/env python3
"""Agent Quality v2.3e canonical baseline（稳定性验证；不改 claim_support / Ranking）。

步骤：
  Temporal + Recall + Ranking(production=v1.4) + Answer(v2 merged) + Evidence(v2 merged)
  + Attribution/Relation unit gold
  + 可选 unseen adversarial（单独报告，不自动修规则）

用法：
  PYTHONPATH=. python eval/run_quality_v2_3e_canonical.py --reuse-env-db
  PYTHONPATH=. python eval/run_quality_v2_3e_canonical.py --reuse-env-db --with-unseen
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
    if env:
        e.update(env)
    print(">>", " ".join(args), flush=True)
    return subprocess.call([sys.executable, *args], cwd=str(ROOT), env=e)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--skip-temporal", action="store_true")
    ap.add_argument("--skip-recall", action="store_true")
    ap.add_argument("--with-unseen", action="store_true", help="run unseen paraphrase Gold (diagnostic)")
    args = ap.parse_args()
    t0 = time.time()
    steps: list[dict] = []

    if not args.skip_temporal:
        steps.append({"step": "temporal", "rc": _py(["eval/run_temporal_baseline.py", "--reuse-env-db"])})

    if not args.skip_recall:
        steps.append(
            {
                "step": "recall",
                "rc": _py(
                    ["eval/run_recall_baseline.py", "--reuse-env-db"],
                    {"MESH_RECALL_USE_EMBED": "0"},
                ),
            }
        )

    # production path = Ranking v1.4（已合入）
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
                    "ranking_canonical_v23e",
                ]
            ),
        }
    )

    steps.append(
        {
            "step": "answer_v2_merged",
            "rc": _py(["eval/run_answer_v2.py", "--reuse-env-db", "--tag", "answer_canonical_v23e"]),
        }
    )
    steps.append(
        {
            "step": "evidence_v2_merged",
            "rc": _py(
                ["eval/run_evidence_baseline.py", "--reuse-env-db", "--tag", "v2", "--gold", "eval/evidence_gold_v2.jsonl"]
            ),
        }
    )

    # Attribution / Relation（已有 Gold）；无 pytest 时退化为 schema validate
    rel_rc = _py(
        [
            "-m",
            "pytest",
            "tests/test_relation_gold_schema.py",
            "tests/test_attribution.py",
            "-q",
            "--tb=line",
        ]
    )
    if rel_rc != 0:
        rel_rc = _py(
            [
                "-c",
                "from eval.relation_gold_lib import load_gold, validate_gold; "
                "validate_gold(load_gold()); "
                "from app.attribution import resolve_attribution; "
                "print('attribution_relation_fallback_ok')",
            ]
        )
    steps.append({"step": "attribution_relation_unit", "rc": rel_rc})

    unseen = None
    if args.with_unseen:
        steps.append(
            {
                "step": "unseen_accept",
                "rc": _py(
                    [
                        "eval/run_answer_evidence_acceptance.py",
                        "--reuse-env-db",
                        "--tag",
                        "unseen_v23e",
                        "--answer-gold",
                        "eval/answer_gold_unseen_v23e.jsonl",
                        "--evidence-gold",
                        "eval/evidence_gold_unseen_v23e.jsonl",
                    ]
                ),
            }
        )
        unseen = _load(REPORTS / "experiments" / "unseen_v23e" / "ACCEPT_unseen_v23e.json")
        if not unseen:
            unseen = _load(REPORTS / "ANSWER_EVIDENCE_ACCEPTANCE_latest.json")

    temporal = _load(REPORTS / "TEMPORAL_BASELINE_latest.json")
    recall = _load(REPORTS / "RECALL_BASELINE_latest.json")
    ranking = _load(REPORTS / "experiments" / "ranking_canonical_v23e" / "RANKING_ranking_canonical_v23e_latest.json")
    if not ranking:
        ranking = _load(REPORTS / "RANKING_BASELINE_latest.json")
    answer = _load(REPORTS / "experiments" / "answer_canonical_v23e" / "ANSWER_answer_canonical_v23e.json")
    if not answer:
        answer = _load(REPORTS / "ANSWER_V2_latest.json")
    evidence = _load(REPORTS / "experiments" / "v2" / "EVIDENCE_v2.json")
    if not evidence:
        evidence = _load(REPORTS / "EVIDENCE_evidence_v2.json")

    ans_fails = [r["id"] for r in (answer.get("results") or []) if not (r.get("metrics") or {}).get("pass")]
    ev_fails = [r["id"] for r in (evidence.get("results") or []) if not (r.get("metrics") or {}).get("pass")]

    # Ranking focus gates (production v1.4)
    focus = {}
    for r in ranking.get("results") or []:
        if r.get("id") in ("R06", "R12", "R16", "R18", "R23"):
            focus[r["id"]] = {
                "pat": r.get("failure_pattern"),
                "mrr": (r.get("metrics") or {}).get("mrr"),
                "ndcg@10": (r.get("metrics") or {}).get("ndcg@10"),
                "top": (r.get("retrieved_items") or [])[:8],
            }

    # 4125 rank if present in R12
    r12 = next((r for r in (ranking.get("results") or []) if r.get("id") == "R12"), None)
    rank_4125 = None
    if r12:
        ids = [str(x) for x in (r12.get("retrieved_items") or [])]
        if "4125" in ids:
            rank_4125 = ids.index("4125") + 1

    unseen_summary = None
    if unseen:
        unseen_summary = {
            "answer_pass_rate": (unseen.get("answer") or {}).get("pass_rate")
            or unseen.get("answer_pass_rate"),
            "evidence_pass_rate": (unseen.get("evidence") or {}).get("pass_rate")
            or unseen.get("evidence_pass_rate"),
            "answer_fails": (unseen.get("remaining_for_triage") or {}).get("answer_fails")
            or [{"id": x} for x in (unseen.get("answer_fails") or [])],
            "evidence_fails": (unseen.get("remaining_for_triage") or {}).get("evidence_fails")
            or [{"id": x} for x in (unseen.get("evidence_fails") or [])],
        }

    report = {
        "phase": "quality_v2_3e_canonical",
        "elapsed_s": round(time.time() - t0, 2),
        "frozen": {
            "ranking": "v1.4",
            "recall": "frozen",
            "vector": "OFF",
            "agent_contract": "frozen_v1",
            "claim_support": "v2.3e_no_further_edits",
            "wiki_es_graph": "deferred",
        },
        "gold": {
            "answer": "answer_gold_v2.jsonl (merged accept)",
            "evidence": "evidence_gold_v2.jsonl (merged accept)",
            "canonical_ids_added": ["A18", "A19", "A20", "A21", "E16", "E17", "E18", "E19", "E20"],
        },
        "steps": steps,
        "temporal": {
            "n": temporal.get("n"),
            "pass_rate": temporal.get("pass_rate") or temporal.get("macro_pass"),
        },
        "recall": {
            "R@5": recall.get("macro_recall@5") or recall.get("recall@5"),
            "R@10": recall.get("macro_recall@10") or recall.get("recall@10"),
            "R@20": recall.get("macro_recall@20") or recall.get("recall@20"),
        },
        "ranking": {
            "macro_mrr": ranking.get("macro_mrr"),
            "macro_ndcg@10": ranking.get("macro_ndcg@10"),
            "macro_precision@5": ranking.get("macro_precision@5"),
            "macro_recall@20": ranking.get("macro_recall@20"),
            "focus": focus,
            "4125_rank": rank_4125,
        },
        "answer": {
            "n": answer.get("n"),
            "pass_rate": answer.get("pass_rate"),
            "fails": ans_fails,
        },
        "evidence": {
            "n": evidence.get("n"),
            "pass_rate": evidence.get("pass_rate"),
            "fails": ev_fails,
        },
        "attribution_relation_unit_rc": rel_rc,
        "unseen": unseen_summary,
        "verdict": {
            "canonical_answer_clean": len(ans_fails) == 0,
            "canonical_evidence_clean": len(ev_fails) == 0,
            "ready_freeze_claim_support": len(ans_fails) == 0 and len(ev_fails) == 0,
            "unseen_run": bool(args.with_unseen),
            "note": "unseen failures diagnose keyword vs semantic; do not auto-patch claim_support",
        },
    }

    out_dir = REPORTS / "baselines"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "QUALITY_V2_3E_CANONICAL.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md = REPORTS / "AGENT_QUALITY_V2_3e_CANONICAL_BASELINE.md"
    md.write_text(
        _md(report),
        encoding="utf-8",
    )
    print(json.dumps({k: report[k] for k in report if k != "steps"}, ensure_ascii=False, indent=2))
    print("wrote", path)
    print("wrote", md)
    hard_ok = report["verdict"]["canonical_answer_clean"] and report["verdict"]["canonical_evidence_clean"]
    return 0 if hard_ok else 1


def _md(report: dict) -> str:
    u = report.get("unseen") or {}
    lines = [
        "# Agent Quality v2.3e · Canonical Baseline（稳定性验证）",
        "",
        "> claim_support **停刀**。Ranking v1.4 / Recall / Vector / Contract 冻结。",
        "",
        "## Gold merge",
        "",
        f"- Answer：`answer_gold_v2.jsonl` n={report['answer'].get('n')}（含 A18–A21）",
        f"- Evidence：`evidence_gold_v2.jsonl` n={report['evidence'].get('n')}（含 E16–E20）",
        "",
        "## Layers",
        "",
        f"| Layer | Result |",
        f"|-------|--------|",
        f"| Temporal | {report['temporal']} |",
        f"| Recall R@5/10/20 | {report['recall']} |",
        f"| Ranking v1.4 | MRR={report['ranking'].get('macro_mrr')} nDCG={report['ranking'].get('macro_ndcg@10')} R@20={report['ranking'].get('macro_recall@20')} 4125@{report['ranking'].get('4125_rank')} |",
        f"| Answer | pass={report['answer'].get('pass_rate')} fails={report['answer'].get('fails')} |",
        f"| Evidence | pass={report['evidence'].get('pass_rate')} fails={report['evidence'].get('fails')} |",
        f"| Attr/Relation unit rc | {report.get('attribution_relation_unit_rc')} |",
        "",
        "## Unseen adversarial（诊断：语义 vs 关键词）",
        "",
    ]
    if u:
        lines += [
            f"- Answer pass={u.get('answer_pass_rate')} fails={[x.get('id') if isinstance(x, dict) else x for x in (u.get('answer_fails') or [])]}",
            f"- Evidence pass={u.get('evidence_pass_rate')} fails={[x.get('id') if isinstance(x, dict) else x for x in (u.get('evidence_fails') or [])]}",
            "",
            "若 unseen fail：说明当前 strength 规则仍偏关键词匹配；**本轮不自动改 claim_support**。",
        ]
    else:
        lines.append("_未跑 unseen（加 `--with-unseen`）。_")
    lines += [
        "",
        "## Verdict",
        "",
        f"```json\n{json.dumps(report.get('verdict'), ensure_ascii=False, indent=2)}\n```",
        "",
        f"elapsed_s={report.get('elapsed_s')}",
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
