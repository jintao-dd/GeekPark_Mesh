#!/usr/bin/env python3
"""最终验收主入口：25 题逐条指标 + 可选 E2E / follow-up / SSE。

本地黄金语料（单期）：
  python eval/run_final_eval.py --corpus golden

生产 PG（在服务器容器内）：
  bash deploy/run_final_acceptance_prod.sh

输出：
  eval/reports/report_<ts>.json
  eval/reports/FINAL_ACCEPTANCE_REPORT.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REPORT_DIR = ROOT / "eval" / "reports"
EVAL_PATH = ROOT / "eval" / "ask_eval_v1.jsonl"


def _setup_corpus(corpus: str):
    from app import db, db_conn
    from eval.run_acceptance import ISSUE_EXPORT, _seed_golden_db

    if corpus == "prod":
        if not (os.environ.get("MESH_DB_URL") or "").startswith("postgres"):
            raise SystemExit("生产模式需要 MESH_DB_URL=postgresql://...")
        return db.connect(), "prod_pg", None

    td = tempfile.mkdtemp(prefix="mesh_eval_")
    db_path = Path(td) / "eval.db"
    old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
    db_conn.DB_PATH = str(db_path)
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = str(db_path)
    con = db.connect()
    db.init_db(seed=True)
    if corpus == "golden":
        _seed_golden_db(con)
    return con, f"golden_sqlite({ISSUE_EXPORT.name})", (old_path, old_url)


def _restore_db(restore):
    if not restore:
        return
    from app import db, db_conn
    db_conn.DB_PATH, db_conn.MESH_DB_URL = restore


def _write_report(payload: dict) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jp = REPORT_DIR / f"report_{ts}.json"
    jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md = _markdown_report(payload)
    mp = REPORT_DIR / "FINAL_ACCEPTANCE_REPORT.md"
    mp.write_text(md, encoding="utf-8")
    return mp


def _markdown_report(p: dict) -> str:
    lines = [
        "# Mesh Ask 最终验收报告",
        "",
        f"- 生成时间：{p.get('generated_at')}",
        f"- 语料环境：**{p.get('corpus_env')}**",
        f"- 生产 PG 全库：**{p.get('prod_pg_verified', '未验证')}**",
        "",
        "## 汇总",
        "",
        f"| 维度 | 结果 |",
        f"|------|------|",
        f"| 25 题检索 | {p.get('retrieval_summary')} |",
        f"| E2E LLM | {p.get('e2e_summary', '未跑')} |",
        f"| 二轮 follow-up | {p.get('followup_summary', '未跑')} |",
        f"| SSE 断线回放 | {p.get('sse_summary', '未跑')} |",
        "",
        "## 已验证 / 未验证 / 已知限制",
        "",
    ]
    for sec in ("verified", "not_verified", "known_limits", "failures"):
        lines.append(f"### {sec}")
        for item in p.get(sec) or []:
            lines.append(f"- {item}")
        lines.append("")

    fl = p.get("failures_by_layer") or {}
    if any(fl.get(k) for k in fl):
        lines.append("## 失败分层")
        lines.append("")
        for layer in ("routing", "retrieval", "evidence", "answer", "other"):
            items = fl.get(layer) or []
            if items:
                lines.append(f"### {layer} ({len(items)})")
                for it in items:
                    lines.append(f"- {it}")
                lines.append("")

    e2e_rows = [r for r in (p.get("questions") or []) if r.get("latency")]
    if e2e_rows:
        lines.append("## E2E 延迟与用量")
        lines.append("")
        lines.append("| ID | retrieve_ms | analysis_ms | total_ms | llm_calls | correctness |")
        lines.append("|----|-------------|-------------|----------|-----------|-------------|")
        for r in e2e_rows:
            lat = r.get("latency") or {}
            us = r.get("usage") or {}
            lines.append(
                f"| {r['id']} | {lat.get('retrieve_ms')} | {lat.get('analysis_ms')} "
                f"| {lat.get('total_ms')} | {us.get('llm_calls', '—')} "
                f"| {(r.get('answer') or {}).get('correctness', '—')} |"
            )
        lines.append("")

    lines.append("## 25 题逐题结果")
    lines.append("")
    lines.append("| ID | PASS | layer | routing | mode | n_ctx | 关键检查 |")
    lines.append("|----|------|-------|---------|------|-------|----------|")
    for r in p.get("questions") or []:
        rc = r.get("routing") or {}
        rec = r.get("recall") or {}
        bad = [c["check"] for c in (r.get("checks") or []) if not c.get("ok")]
        lines.append(
            f"| {r['id']} | {'Y' if r.get('pass') else 'N'} | {r.get('failure_layer') or '—'} "
            f"| {rc.get('kind')} | {rec.get('mode')} | {rec.get('n_context')} | {', '.join(bad) or '—'} |"
        )
    lines.append("")
    lines.append("## 逐题详情")
    lines.append("")
    for r in p.get("questions") or []:
        lines.append(f"### {r['id']}: {r['q']}")
        lines.append(f"- tags: {', '.join(r.get('tags') or [])}")
        lines.append(f"- routing: `{json.dumps(r.get('routing'), ensure_ascii=False)}`")
        lines.append(f"- recall: `{json.dumps(r.get('recall'), ensure_ascii=False)}`")
        if r.get("rerank"):
            lines.append(f"- rerank/team baseline: `{json.dumps(r.get('rerank'), ensure_ascii=False)}`")
        if r.get("answer"):
            lines.append(f"- answer ({r['answer'].get('len')} chars): {r['answer'].get('text','')[:300]}…")
        if r.get("latency"):
            lines.append(f"- latency: `{json.dumps(r.get('latency'), ensure_ascii=False)}`")
        if r.get("usage"):
            lines.append(f"- usage: `{json.dumps(r.get('usage'), ensure_ascii=False)}`")
        if r.get("verify"):
            lines.append(f"- verify: `{json.dumps(r.get('verify'), ensure_ascii=False)}`")
        for c in r.get("checks") or []:
            mark = "ok" if c.get("ok") else "**FAIL**"
            lines.append(f"  - {c.get('check')}: {mark} — {c.get('detail')}")
        lines.append("")

    if p.get("followup_e2e"):
        lines.append("## 二轮 Follow-up E2E")
        lines.append("```json")
        lines.append(json.dumps(p["followup_e2e"], ensure_ascii=False, indent=2))
        lines.append("```")
    if p.get("sse_replay"):
        lines.append("## SSE 断线回放")
        lines.append("```json")
        lines.append(json.dumps(p["sse_replay"], ensure_ascii=False, indent=2))
        lines.append("```")
    return "\n".join(lines)


def _write_phase1_closeout(p: dict) -> Path:
    """Phase 1 收口报告（独立于 FINAL_ACCEPTANCE_REPORT）。"""
    lines = [
        "# Phase 1 Closeout Report",
        "",
        f"- 生成时间：{p.get('generated_at')}",
        f"- 语料：**{p.get('corpus_env')}**",
        f"- 生产 PG：**{p.get('prod_pg_verified', '未验证')}**",
        f"- 总耗时：{p.get('elapsed_sec')}s",
        "",
        "## 验收摘要",
        "",
        f"| 项 | 结果 |",
        f"|----|------|",
        f"| 检索 | {p.get('retrieval_summary')} |",
        f"| E2E | {p.get('e2e_summary', '未跑')} |",
        f"| Follow-up | {p.get('followup_summary', '未跑')} |",
        f"| SSE | {p.get('sse_summary', '未跑')} |",
        "",
        "## Query Guard",
        "",
    ]
    guard_cases = [r for r in (p.get("questions") or []) if "guard" in (r.get("tags") or [])]
    for r in guard_cases:
        rec = r.get("recall") or {}
        lines.append(
            f"- **{r['id']}** pass={r.get('pass')} mode={rec.get('mode')} "
            f"n_hits={rec.get('n_hits')} direct={rec.get('direct_answer')}"
        )
    lines.append("")
    fl = p.get("failures_by_layer") or {}
    lines.append("## 失败分层")
    lines.append("")
    for layer in ("routing", "retrieval", "evidence", "answer", "other"):
        items = fl.get(layer) or []
        lines.append(f"- **{layer}** ({len(items)}): {', '.join(items) if items else '—'}")
    lines.append("")
    lines.append("## SSE 验收标准（Phase 1）")
    lines.append("")
    lines.append("- analysis_id 可回放")
    lines.append("- answer / context_refs / sources / evidence_refs 持久化")
    lines.append("- status=completed")
    lines.append("- GET /api/ask/analysis/{id} 稳定")
    lines.append("- **不**要求 token prefix 与二次 LLM 一致")
    lines.append("")
    if p.get("sse_replay"):
        lines.append(f"SSE 实测：**{p.get('sse_summary')}**")
        lines.append("")
    lines.append("## 下一步")
    lines.append("")
    lines.append("- Phase 1 完成后进入 Planner-lite（纯规则 plan_retrieval）")
    lines.append("- 不为 e08/e10/e21 强制 structured")
    lines.append("")
    path = REPORT_DIR / "PHASE1_CLOSEOUT_REPORT.md"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def run_retrieval_suite(con, cases, *, e2e: bool, e2e_all: bool = False) -> list[dict]:
    from eval.eval_lib import evaluate_e2e, evaluate_retrieval

    rows = []
    for case in cases:
        row = evaluate_retrieval(con, case)
        if e2e and (e2e_all or case.get("e2e")):
            row = evaluate_e2e(con, case, row)
        # strip heavy prepared
        row.pop("prepared", None)
        row.pop("route", None)
        rows.append(row)
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", choices=("golden", "prod"), default="golden")
    parser.add_argument("--e2e", action="store_true")
    parser.add_argument(
        "--e2e-all",
        action="store_true",
        help="With --e2e: run analysis E2E for every case (Baseline freeze needs latency.total_ms + usage on all 25)",
    )
    parser.add_argument("--followup", action="store_true")
    parser.add_argument("--sse", action="store_true")
    args = parser.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")

    from eval.eval_lib import db_stats, load_cases, summarize_failures_by_layer

    cases = load_cases(EVAL_PATH)
    con, env_label, restore = _setup_corpus(args.corpus)
    stats = db_stats(con)
    print(f"语料: {env_label} stats={stats}")

    t0 = time.time()
    payload: dict = {}
    exit_code = 0
    try:
        if args.e2e_all and not args.e2e:
            print("note: --e2e-all implies --e2e", flush=True)
            args.e2e = True
        questions = run_retrieval_suite(
            con, cases, e2e=args.e2e, e2e_all=args.e2e_all,
        )
        retr_pass = sum(1 for r in questions if r.get("pass"))
        payload = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "corpus_env": env_label,
            "db_stats": stats,
            "questions": questions,
            "retrieval_summary": f"{retr_pass}/{len(cases)} pass",
            "e2e_mode": "all" if args.e2e_all else ("tagged" if args.e2e else "off"),
            "verified": [],
            "not_verified": [],
            "known_limits": [],
            "failures": [],
        }

        if args.corpus == "prod" and stats.get("issues_published", 0) > 1:
            payload["prod_pg_verified"] = "是（多期 published）"
            payload["verified"].append("生产 PG 全库多期语料已加载")
        elif args.corpus == "prod":
            payload["prod_pg_verified"] = f"部分（published 期数={stats.get('issues_published')}）"
        else:
            payload["prod_pg_verified"] = "否（本地黄金 SQLite）"
            if args.corpus == "golden":
                payload["not_verified"].append("生产 PG 全库（需在 prod 容器重跑）")

        payload["known_limits"].append("黄金语料以 2026-8-17 为主，不等同生产 PG 全库")
        payload["known_limits"].append("LLM E2E 存在输出波动")

        for r in questions:
            if not r.get("pass"):
                layer = r.get("failure_layer") or "other"
                bad = [c["check"] for c in r.get("checks", []) if not c.get("ok")]
                payload["failures"].append(f"{r['id']}({layer}): {bad}")

        payload["failures_by_layer"] = summarize_failures_by_layer(questions)

        if args.followup:
            from eval.test_followup_e2e import run_followup_e2e
            payload["followup_e2e"] = run_followup_e2e(con)
            fu = payload["followup_e2e"]
            payload["followup_summary"] = f"{'PASS' if fu.get('pass') else 'FAIL'} — {fu.get('summary')}"
            if fu.get("pass"):
                payload["verified"].append("二轮 follow-up E2E")
            else:
                payload["failures"].append(f"followup_e2e: {fu.get('summary')}")

        if args.sse:
            from eval.test_sse_replay import run_sse_replay_test
            payload["sse_replay"] = run_sse_replay_test(con)
            sr = payload["sse_replay"]
            payload["sse_summary"] = f"{'PASS' if sr.get('pass') else 'FAIL'} — {sr.get('summary')}"
            if sr.get("pass"):
                payload["verified"].append("SSE 断线回放")
            else:
                payload["failures"].append(f"sse_replay: {sr.get('summary')}")

        if args.e2e:
            ep = sum(1 for c in cases if c.get("e2e") for r in questions if r["id"] == c["id"] and r.get("pass"))
            payload["e2e_summary"] = f"{ep}/{sum(1 for c in cases if c.get('e2e'))} e2e pass"

        payload["verified"].append(f"25 题逐条指标（检索 {retr_pass}/{len(cases)} 通过）")
        payload["elapsed_sec"] = int(time.time() - t0)
        _write_phase1_closeout(payload)
        if payload["failures"]:
            exit_code = 1
    except Exception as e:
        import traceback
        payload.setdefault("failures", []).append(f"runner_exception: {e}")
        payload["traceback"] = traceback.format_exc()
        exit_code = 1
    finally:
        con.close()
        _restore_db(restore)
        if payload.get("questions"):
            mp = _write_report(payload)
            print(f"\n报告已写入 {mp}")

    print(f"检索: {payload.get('retrieval_summary', 'N/A')}")
    if payload.get("failures"):
        print("失败项:", payload["failures"])
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
