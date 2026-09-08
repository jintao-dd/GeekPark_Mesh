#!/usr/bin/env python3
"""Temporal Phase 0 · Baseline runner（只读现状，不改 RAG / Retrieval / Ranking / Agent 主逻辑）。

用法：
  MESH_AGENT_USE_LLM=1 PYTHONPATH=. python eval/run_temporal_baseline.py --reuse-env-db
  MESH_AGENT_USE_LLM=1 python eval/run_temporal_baseline.py --reuse-env-db --limit 3

完成条件：Gold 可跑通 + 得到失败分布（不要求 24/24 PASS）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD_PATH = ROOT / "eval" / "temporal_gold_v1.jsonl"
OPEN_ID = "ou_agent_temporal"

RECENT_EVENT = re.compile(
    r"(最近发生|本周发生|上周发生|刚刚发生|刚发生|昨天发生|正在发生|"
    r"最近正在|本周还在|本周刚|这周刚|近日发生|近期发生了)"
)
THIS_WEEK_EVENT = re.compile(r"(本周发生|这周发生|本周刚|这周刚|本周正在|本周还在)")
LAST_WEEK_EVENT = re.compile(r"(上周发生|上周刚|上周正在)")
YESTERDAY = re.compile(r"(昨天发生|昨日发生|昨天刚)")
JUST_HAPPENED = re.compile(r"(刚发生|刚刚发生|才发生|刚结束)")
WALL_THIS_WEEK = re.compile(r"(本周|这周)")
WALL_LAST_WEEK = re.compile(r"上周")
CAVEAT = re.compile(r"(并非今日|不是今天|依据的是|该期|已上线记录|资料未提供|未提供发生时间|尚未确认)")


def _load_gold() -> list[dict]:
    rows = []
    for line in GOLD_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        rows.append(json.loads(line))
    return rows


def _disable_embed() -> None:
    from app import embeddings

    embeddings.embed_one = lambda _t: []  # type: ignore[assignment]
    embeddings.is_configured = lambda: False  # type: ignore[assignment]


def _ensure_user(con) -> None:
    row = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (OPEN_ID,)
    ).fetchone()
    if row:
        con.execute(
            "UPDATE users SET team=?, role=? WHERE feishu_open_id=?",
            ("编辑部", "viewer", OPEN_ID),
        )
    else:
        con.execute(
            "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
            "VALUES ('agent_temporal','Agent Temporal','x','viewer','编辑部',?)",
            (OPEN_ID,),
        )
    con.commit()


def _infer_predicted(answer: str, issue_mode: str) -> tuple[str, str]:
    """系统尚未输出 time_semantics；从成文启发式标注（baseline 用）。"""
    text = answer or ""
    if re.search(r"资料未提供发生时间|未提供.*时间|无法确认.*时间|尚未确认", text):
        basis = "unknown"
    elif CAVEAT.search(text) and re.search(r"\d{4}-\d{1,2}-\d{1,2}|\d{4}\.\d{1,2}|期", text):
        basis = "issue_time"
    elif RECENT_EVENT.search(text):
        basis = "event_time_claimed"  # 声称有事件时间语义，未必真有
    else:
        basis = "unobserved"

    if THIS_WEEK_EVENT.search(text) or (
        WALL_THIS_WEEK.search(text) and RECENT_EVENT.search(text)
    ):
        window = "this_week"
    elif LAST_WEEK_EVENT.search(text):
        window = "last_week"
    elif RECENT_EVENT.search(text) or re.search(r"最近|近期", text):
        window = "recent"
    elif issue_mode in ("explicit", "pinned", "latest_published"):
        window = "issue_scoped"
    else:
        window = "unobserved"
    return basis, window


def _grade(row: dict, *, answer: str, issue_slug: str, issue_mode: str) -> tuple[bool, str]:
    g = row.get("grade") or {}
    text = answer or ""
    reasons: list[str] = []

    if g.get("forbid_recent_event_claim") and RECENT_EVENT.search(text):
        if not CAVEAT.search(text):
            reasons.append("forbid_recent_event_claim")
    if g.get("forbid_this_week_as_event") and THIS_WEEK_EVENT.search(text):
        reasons.append("forbid_this_week_as_event")
    if g.get("forbid_just_happened") and JUST_HAPPENED.search(text):
        reasons.append("forbid_just_happened")
    if g.get("forbid_yesterday_as_wall_clock") and YESTERDAY.search(text):
        reasons.append("forbid_yesterday_as_wall_clock")
    if g.get("forbid_wall_clock_last_week"):
        if WALL_LAST_WEEK.search(text) and not CAVEAT.search(text) and "墙上时钟" not in text and "已定点次" not in text:
            reasons.append("forbid_wall_clock_last_week")
    if g.get("forbid_wall_clock_this_week"):
        # 说「本周」却无期次/依据 caveat → 疑似墙上时钟
        if WALL_THIS_WEEK.search(text) and not CAVEAT.search(text) and not issue_slug and "墙上时钟" not in text:
            reasons.append("forbid_wall_clock_this_week")
    if g.get("forbid_today_as_event") and re.search(r"今日新发生|今天刚|今天发生", text):
        reasons.append("forbid_today_as_event")

    req_slug = g.get("require_issue_slug")
    if req_slug and issue_slug and issue_slug != req_slug:
        reasons.append(f"require_issue_slug:got={issue_slug}")
    if req_slug and not issue_slug:
        reasons.append("require_issue_slug:missing")

    wrong = g.get("forbid_wrong_issue")
    if wrong and wrong in text and (not issue_slug or issue_slug == wrong):
        # 回答正文串到错误期且 IssueRef 也偏了，或正文主推错误期
        if issue_slug == wrong or (wrong in text and req_slug and req_slug not in text):
            reasons.append(f"forbid_wrong_issue:{wrong}")

    if g.get("must_reject_latest_equals_recent"):
        # 若把最新上线直接当最近发生且无拒绝/纠正
        if RECENT_EVENT.search(text) and not re.search(
            r"不等于|并非|不能.*当成|上线不等于|不是最近发生|资料未提供", text
        ):
            reasons.append("must_reject_latest_equals_recent")

    if g.get("require_issue_or_period_mention"):
        if not (
            CAVEAT.search(text)
            or re.search(r"\d{4}-\d{1,2}-\d{1,2}|20\d{2}\.\d{1,2}|期次|该期", text)
            or issue_mode in ("explicit", "pinned", "latest_published")
        ):
            reasons.append("require_issue_or_period_mention")

    # unknown basis：确定性「最近/本周发生」一律失败
    if (row.get("time_semantics") or {}).get("basis") == "unknown":
        if RECENT_EVENT.search(text) or THIS_WEEK_EVENT.search(text):
            if "forbid_recent_event_claim" not in reasons:
                reasons.append("unknown_basis_but_claimed_recent")

    if reasons:
        return False, ";".join(reasons)
    return True, ""


def _ask(con, query: str, *, explicit_issue: str = "") -> dict:
    from app.agent.harness import run_harness

    payload = {
        "text": query,
        "channel": "harness",
        "feishu_open_id": OPEN_ID,
    }
    if explicit_issue:
        payload["explicit_issue"] = explicit_issue
    return run_harness(con, payload)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--ids", default="", help="comma ids e.g. T01,T13")
    args = ap.parse_args()

    os.environ.setdefault("MESH_AGENT_USE_LLM", "1")
    _disable_embed()

    from app import db

    gold = _load_gold()
    if args.ids.strip():
        want = {x.strip() for x in args.ids.split(",") if x.strip()}
        gold = [r for r in gold if r.get("id") in want]
    if args.limit and args.limit > 0:
        gold = gold[: args.limit]

    con = db.connect()
    _ensure_user(con)

    results: list[dict] = []
    t0 = time.time()
    try:
        for i, row in enumerate(gold, 1):
            qid = row.get("id")
            query = row["query"]
            scope = row.get("issue_scope") or "none"
            explicit = ""
            if scope == "explicit_issue":
                explicit = (row.get("explicit_issue") or "").strip()
            print(f"[{i}/{len(gold)}] {qid} {query[:40]}…", flush=True)
            t1 = time.time()
            try:
                ans = _ask(con, query, explicit_issue=explicit)
            except Exception as e:
                ans = {"text": "", "error": str(e), "intent": "error"}
            elapsed = int((time.time() - t1) * 1000)
            text = ans.get("text") or ""
            ctx = ans.get("context") or {}
            iref = ctx.get("issue_ref") or {}
            issue_slug = iref.get("slug") or ""
            issue_mode = iref.get("mode") or ""
            # map mode to gold-ish scope label
            if issue_mode == "explicit":
                obs_scope = "explicit_issue"
            elif issue_mode == "latest_published":
                obs_scope = "latest_published"
            elif issue_mode == "none":
                obs_scope = "none"
            else:
                obs_scope = issue_mode or "unobserved"

            pred_basis, pred_window = _infer_predicted(text, issue_mode)
            # Phase 1：优先采用系统输出的 temporal
            sys_t = (ans.get("trace") or {}).get("temporal") or {}
            if isinstance(sys_t, dict) and sys_t.get("basis"):
                pred_basis = sys_t.get("basis") or pred_basis
                pred_window = sys_t.get("window") or pred_window
            ok, reason = _grade(
                row, answer=text, issue_slug=issue_slug, issue_mode=issue_mode
            )
            evidence = ans.get("evidence_refs") or []
            results.append(
                {
                    "id": qid,
                    "category": row.get("category"),
                    "query": query,
                    "gold_time_semantics": row.get("time_semantics"),
                    "gold_issue_scope": scope,
                    "gold_explicit_issue": explicit or None,
                    "expected_behavior": row.get("expected_behavior"),
                    "predicted_time_basis": pred_basis,
                    "predicted_window": pred_window,
                    "system_temporal": sys_t or None,
                    "issue_scope": obs_scope,
                    "issue_slug": issue_slug,
                    "retrieved_evidence": evidence[:12],
                    "final_answer": text[:1200],
                    "intent": ans.get("intent"),
                    "data_tools": ans.get("data_tools_called") or [],
                    "pass": ok,
                    "failure_reason": reason,
                    "elapsed_ms": elapsed,
                    "note": (
                        "system_temporal from Agent Phase 1"
                        if sys_t
                        else "predicted_* inferred from answer; no system temporal"
                    ),
                }
            )
            print(
                f"  → pass={ok} basis={pred_basis} scope={obs_scope}:{issue_slug} "
                f"fail={reason or '-'} {elapsed}ms",
                flush=True,
            )
    finally:
        con.close()

    by_cat = Counter()
    fail_reasons = Counter()
    n_pass = sum(1 for r in results if r["pass"])
    for r in results:
        by_cat[r["category"]] += 1
        if not r["pass"]:
            for part in (r["failure_reason"] or "unknown").split(";"):
                fail_reasons[part.split(":")[0]] += 1

    out = {
        "phase": "Temporal Phase 0 Baseline",
        "gold": str(GOLD_PATH.name),
        "n": len(results),
        "n_pass": n_pass,
        "n_fail": len(results) - n_pass,
        "pass_rate": round(n_pass / len(results), 4) if results else 0,
        "elapsed_s": round(time.time() - t0, 1),
        "by_category_count": dict(by_cat),
        "failure_reason_hist": dict(fail_reasons),
        "completion_criteria": {
            "gold_built": True,
            "baseline_ran": True,
            "all_pass_required": False,
            "next": "minimal Temporal implement only after reviewing failure distribution",
        },
        "results": results,
    }

    stamp = time.strftime("%Y%m%d_%H%M%S")
    report = ROOT / "eval" / "reports" / f"TEMPORAL_BASELINE_{stamp}.json"
    latest = ROOT / "eval" / "reports" / "TEMPORAL_BASELINE_latest.json"
    payload = json.dumps(out, ensure_ascii=False, indent=2)
    report.write_text(payload, encoding="utf-8")
    latest.write_text(payload, encoding="utf-8")

    md = ROOT / "eval" / "reports" / "TEMPORAL_BASELINE_latest.md"
    lines = [
        "# Temporal Phase 0 · Baseline",
        "",
        f"- Gold: `{GOLD_PATH.name}` · n={out['n']}",
        f"- Pass: **{n_pass}/{out['n']}** ({out['pass_rate']:.0%}) — **不要求全过**",
        f"- Elapsed: {out['elapsed_s']}s",
        "",
        "## Failure reason histogram",
        "",
    ]
    for k, v in fail_reasons.most_common():
        lines.append(f"- `{k}`: {v}")
    if not fail_reasons:
        lines.append("- (none)")
    lines += ["", "## Per-item", ""]
    for r in results:
        mark = "PASS" if r["pass"] else "FAIL"
        lines.append(
            f"- **{r['id']}** [{r['category']}] {mark} · basis={r['predicted_time_basis']} · "
            f"scope={r['issue_scope']}:{r['issue_slug'] or '-'} · {r['failure_reason'] or '-'}"
        )
        lines.append(f"  - Q: {r['query']}")
    md.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(payload[:2500])
    print(f"wrote {report}")
    print(f"wrote {latest}")
    print(f"wrote {md}")
    # Phase 0 success = ran, not all-pass
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
