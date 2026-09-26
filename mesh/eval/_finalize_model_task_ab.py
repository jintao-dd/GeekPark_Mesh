#!/usr/bin/env python3
"""Merge opus_all disk reports + latest TASK_AB partial into frozen QUALITY_MODEL_AB."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "eval" / "reports"
OPUS = "anthropic/claude-4.8-opus"
SONNET = "claude-sonnet-4-6"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _pack(tag: str, cfg: dict) -> dict:
    def rep(kind: str, suf: str) -> dict:
        if kind == "ans":
            return _load(
                REPORTS / "experiments" / f"taskab_{tag}_{suf}" / f"ANSWER_taskab_{tag}_{suf}.json"
            )
        return _load(
            REPORTS / "experiments" / f"taskab_{tag}_{suf}" / f"EVIDENCE_taskab_{tag}_{suf}.json"
        )

    ans_c, ev_c, ans_u, ev_u = rep("ans", "ans_c"), rep("ev", "ev_c"), rep("ans", "ans_u"), rep("ev", "ev_u")
    usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0}
    for r in (ans_c, ev_c, ans_u, ev_u):
        u = r.get("usage") or {}
        for k in usage:
            usage[k] = int(usage[k]) + int(u.get(k) or 0)

    def pr(r):
        return float(r["pass_rate"]) if r and "pass_rate" in r else None

    def fails(r):
        return [x["id"] for x in (r.get("results") or []) if not (x.get("metrics") or {}).get("pass")]

    def llm_n(r):
        return sum(1 for x in (r.get("results") or []) if x.get("llm_used"))

    metrics = {
        "answer_canonical": pr(ans_c),
        "evidence_canonical": pr(ev_c),
        "answer_unseen": pr(ans_u),
        "evidence_unseen": pr(ev_u),
        "answer_canonical_elapsed_s": ans_c.get("elapsed_s"),
        "evidence_canonical_elapsed_s": ev_c.get("elapsed_s"),
        "answer_unseen_elapsed_s": ans_u.get("elapsed_s"),
        "evidence_unseen_elapsed_s": ev_u.get("elapsed_s"),
        "answer_canonical_llm_used": llm_n(ans_c),
        "answer_unseen_llm_used": llm_n(ans_u),
        "fail_ids": {
            "answer_canonical": fails(ans_c),
            "evidence_canonical": fails(ev_c),
            "answer_unseen": fails(ans_u),
            "evidence_unseen": fails(ev_u),
        },
        "usage": usage,
    }
    quality_ok = all(
        metrics[k] is not None and abs(metrics[k] - 1.0) < 1e-9
        for k in ("answer_canonical", "evidence_canonical", "answer_unseen", "evidence_unseen")
    )
    # cost rough
    rate = {
        OPUS: (0.5, 25.0),
        SONNET: (None, None),
    }.get(cfg["answer"], (None, None))
    cost = {"usd_estimate": None, "note": "sonnet rate unset on modelink snapshot"}
    if rate[0] is not None and rate[1] is not None:
        cost = {
            "usd_estimate": round(
                usage["prompt_tokens"] / 1e6 * rate[0] + usage["completion_tokens"] / 1e6 * rate[1],
                6,
            ),
            "rate_model": cfg["answer"],
            "note": "modelink opus cache_input proxy + output $25/M",
        }
    return {
        "config": cfg,
        "metrics": metrics,
        "quality_ok": quality_ok,
        "cost": cost,
        "wall_s": sum(
            float(x or 0)
            for x in (
                ans_c.get("elapsed_s"),
                ev_c.get("elapsed_s"),
                ans_u.get("elapsed_s"),
                ev_u.get("elapsed_s"),
            )
        ),
    }


def main() -> int:
    partial = _load(REPORTS / "baselines" / "QUALITY_MODEL_TASK_AB.json")
    by_id = {r["config"]["id"]: r for r in (partial.get("configs") or [])}
    # always rebuild from disk for consistency
    configs = [
        _pack(
            "opus_all",
            {
                "id": "opus_all",
                "label": "全 Opus（对照）",
                "semantic": OPUS,
                "answer": OPUS,
                "role": "baseline",
            },
        ),
        _pack(
            "sonnet_all",
            {
                "id": "sonnet_all",
                "label": "全 Sonnet（默认候选）",
                "semantic": SONNET,
                "answer": SONNET,
                "role": "default_candidate",
            },
        ),
        _pack(
            "sonnet_sem_opus_ans",
            {
                "id": "sonnet_sem_opus_ans",
                "label": "semantic=Sonnet / answer=Opus（敏感成文）",
                "semantic": SONNET,
                "answer": OPUS,
                "role": "sensitive_compose",
            },
        ),
    ]
    # prefer partial wall_s if present
    for c in configs:
        if c["config"]["id"] in by_id and by_id[c["config"]["id"]].get("wall_s"):
            c["wall_s"] = by_id[c["config"]["id"]]["wall_s"]

    t_rep = _load(REPORTS / "TEMPORAL_BASELINE_latest.json") or _load(
        REPORTS / "baselines" / "TEMPORAL_BASELINE_v1.json"
    )
    all_ok = all(c.get("quality_ok") for c in configs)
    table = {
        "Claim semantic judge": SONNET,
        "Answer compose (default)": SONNET,
        "Answer compose (sensitive/external)": OPUS,
        "Evidence label": "claim_support v2.4c-2 (frozen; model-light)",
        "Temporal": "residual; do not patch claim_support",
    }
    report = {
        "phase": "quality_model_task_ab",
        "status": "frozen" if all_ok else "needs_review",
        "frozen_layers": {
            "retrieval_ranking": "frozen",
            "claim_support": "v2.4c-2_frozen_no_patch",
            "agent_contract": "frozen",
            "ontology": "frozen_doc_only",
            "gold": "unchanged",
            "dynamic_router": "forbidden",
        },
        "configs": configs,
        "temporal_residual": {
            "pass_rate": t_rep.get("pass_rate"),
            "n_fail": t_rep.get("n_fail"),
            "note": "Independent residual; do not modify Claim Support to chase 1.0",
        },
        "task_model_table": table if all_ok else None,
        "decision": {
            "default": "sonnet_all",
            "sensitive_compose": "sonnet_sem_opus_ans",
            "note": "Quality gap negligible across configs when all quality_ok; prefer Sonnet default, Opus for sensitive answer compose.",
        },
    }
    out = REPORTS / "baselines" / "QUALITY_MODEL_TASK_AB.json"
    ab = REPORTS / "baselines" / "QUALITY_MODEL_AB.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ab.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    # md
    lines = [
        "# Agent Quality · 任务级模型配置（冻结）",
        "",
        "> Claim Support **v2.4c-2 frozen** · 无动态 router · Gold/Contract/Ranking 不变",
        "",
        f"**status=`{report['status']}`**",
        "",
        "## 任务 → 模型（固定配置表）",
        "",
        "| 任务 | 模型 |",
        "|------|------|",
    ]
    for k, v in (report.get("task_model_table") or {}).items():
        lines.append(f"| {k} | `{v}` |")
    lines += ["", "## 配置对比", ""]
    for r in configs:
        c, m = r["config"], r["metrics"]
        lines += [
            f"### `{c['id']}` — {c['label']}",
            "",
            f"- semantic=`{c['semantic']}` · answer=`{c['answer']}`",
            f"- quality_ok=`{r['quality_ok']}` · wall≈{r.get('wall_s')}s",
            f"- Answer C/U: {m.get('answer_canonical')} / {m.get('answer_unseen')} "
            f"(elapsed {m.get('answer_canonical_elapsed_s')}s / {m.get('answer_unseen_elapsed_s')}s)",
            f"- Evidence C/U: {m.get('evidence_canonical')} / {m.get('evidence_unseen')}",
            f"- tokens: `{m.get('usage')}` · cost≈`{(r.get('cost') or {}).get('usd_estimate')}`",
            f"- fails: `{m.get('fail_ids')}`",
            "",
        ]
    tr = report["temporal_residual"]
    lines += [
        "## Temporal residual（单列）",
        "",
        f"- pass_rate=`{tr.get('pass_rate')}` · n_fail=`{tr.get('n_fail')}`",
        "- **禁止**为追 Temporal 1.0 修改已冻结 Claim Support",
        "",
        "## 下一步",
        "",
        "进入 Agent / Tool 设计；用 frozen Gold / Contract / Claim Support 验收 Agent。",
        "",
    ]
    md = "\n".join(lines)
    (REPORTS / "AGENT_QUALITY_MODEL_TASK_AB.md").write_text(md, encoding="utf-8")
    (REPORTS / "AGENT_QUALITY_MODEL_AB.md").write_text(md, encoding="utf-8")
    print(json.dumps({k: report[k] for k in report if k != "configs"}, ensure_ascii=False, indent=2))
    print("wrote", out)
    return 0 if report["status"] == "frozen" else 1


if __name__ == "__main__":
    raise SystemExit(main())
