#!/usr/bin/env python3
"""Layer 3.1：Agent / Ask 性能基线（P50 / P95 / P99）。

回答：Agent 相对 Ask 增加多少延迟？分段 Identity→…→LLM。

用法：
  MESH_AGENT_USE_LLM=1 python eval/run_agent_perf.py --reuse-env-db --rounds 8
  MESH_AGENT_USE_LLM=0 python eval/run_agent_perf.py --reuse-env-db --rounds 8  # 无 LLM 对照

不测 Preview Job 墙钟（见 Final 待补）；本脚本只测请求内同步路径。
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

ISSUE = "2026-8-17"
OPEN_ID = "ou_agent_perf"
Q_ASK = "编辑部关注了哪些话题或公司"
Q_REL = "两边有没有交集关系"


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


def _summary(xs: list[float]) -> dict:
    xs = [float(x) for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {
        "n": len(xs),
        "mean_ms": round(statistics.mean(xs), 1),
        "p50_ms": round(_pct(xs, 50), 1),
        "p95_ms": round(_pct(xs, 95), 1),
        "p99_ms": round(_pct(xs, 99), 1),
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
        "VALUES ('agent_perf','Agent Perf','x','viewer','编辑部',?)",
        (OPEN_ID,),
    )
    con.commit()


def _time_ms(fn) -> tuple[float, object]:
    t0 = time.perf_counter()
    out = fn()
    return (time.perf_counter() - t0) * 1000.0, out


def measure_agent_segments(con, q: str, *, use_llm: bool, issue: str = ISSUE) -> dict:
    from app.agent import context as ctxmod
    from app.agent import identity as idmod
    from app.agent import intent as intentmod
    from app.agent import permission as permmod
    from app.agent import tools as toolsmod
    from app.agent.models import AgentEnvelope
    from app.agent.runtime import handle_message

    os.environ["MESH_AGENT_USE_LLM"] = "1" if use_llm else "0"
    env = AgentEnvelope(
        text=q,
        channel="harness",
        feishu_open_id=OPEN_ID,
        explicit_issue=issue,
    )

    id_ms, identity = _time_ms(lambda: idmod.resolve_identity(con, env))
    chat_ms, chat_team = _time_ms(lambda: ctxmod.chat_team_of(con, env.chat_id))
    perm_ms, permission = _time_ms(
        lambda: permmod.decide_permission(
            identity, explicit_team=env.explicit_team, chat_team=chat_team
        )
    )
    ctx_ms, context = _time_ms(
        lambda: ctxmod.assemble_context(con, env, identity, permission)
    )
    intent_ms, intent = _time_ms(
        lambda: intentmod.rule_classify_intent(env.text, context, permission)
    )
    tool_id = intentmod.intent_to_tool(intent)

    tool_ms = 0.0
    llm_flag = None
    if tool_id:
        tool_ms, result = _time_ms(
            lambda: toolsmod.invoke_tool(
                tool_id, con, identity, permission, context, {"q": q}
            )
        )
        if isinstance(result.payload, dict):
            llm_flag = result.payload.get("llm_used")

    total_ms, ans = _time_ms(lambda: handle_message(con, env))
    return {
        "identity_ms": id_ms,
        "chat_team_ms": chat_ms,
        "permission_ms": perm_ms,
        "context_ms": ctx_ms,
        "intent_ms": intent_ms,
        "tool_ms": tool_ms,
        "agent_total_ms": total_ms,
        "llm_used": llm_flag if llm_flag is not None else (ans.trace or {}).get("llm_used"),
        "intent": ans.intent,
        "evidence_n": len(ans.evidence_refs or []),
    }


def measure_ask(con, q: str, *, issue: str = ISSUE) -> dict:
    from app.ask_scope import AskScope
    from app import ask_engine

    scope = AskScope(
        channel="harness",
        slug=issue,
        team="编辑部",
        user_id=None,
        feishu_open_id=OPEN_ID,
        role="viewer",
        user_team="编辑部",
    )
    row = con.execute(
        "SELECT date_start, date_end FROM issues WHERE slug=?", (issue,)
    ).fetchone()
    if row:
        scope.date_from = (row["date_start"] or "").strip() or None
        scope.date_to = (row["date_end"] or "").strip() or None

    prep_ms, prepared = _time_ms(lambda: ask_engine.prepare(con, q, scope))
    llm_ms = 0.0
    llm_used = False
    answer = (prepared.get("direct_answer") or "").strip()
    ctxs = list(prepared.get("contexts") or [])
    if not answer and ctxs and os.environ.get("MESH_AGENT_USE_LLM", "").strip() in (
        "1",
        "true",
        "yes",
    ):
        from app import llm

        def _llm():
            return llm.answer_question(q, ctxs[:12])

        llm_ms, raw = _time_ms(_llm)
        llm_used = bool(raw)
        answer = (str(raw) if raw else "").strip()
    total = prep_ms + llm_ms
    return {
        "ask_prepare_ms": prep_ms,
        "ask_llm_ms": llm_ms,
        "ask_total_ms": total,
        "llm_used": llm_used,
        "n_context": prepared.get("n_context") or len(ctxs),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reuse-env-db", action="store_true", required=True)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--issue", default=ISSUE)
    args = ap.parse_args()
    issue = args.issue

    use_llm = os.environ.get("MESH_AGENT_USE_LLM", "").strip() in ("1", "true", "yes")
    from app import db

    con = db.connect()
    out: dict = {
        "issue": issue,
        "rounds": args.rounds,
        "mesh_agent_use_llm": use_llm,
        "note": "Preview/Pipeline wall-clock not included; Final Acceptance item.",
    }
    try:
        _ensure_user(con)
        pub = con.execute(
            "SELECT status FROM issues WHERE slug=?", (issue,)
        ).fetchone()
        if not pub or pub["status"] != "published":
            print(f"FAIL: {issue} not published")
            return 2

        # warmup
        for _ in range(max(0, args.warmup)):
            measure_agent_segments(con, Q_ASK, use_llm=use_llm, issue=issue)
            measure_ask(con, Q_ASK, issue=issue)

        agent_rows = []
        ask_rows = []
        for i in range(args.rounds):
            a = measure_agent_segments(con, Q_ASK, use_llm=use_llm, issue=issue)
            b = measure_ask(con, Q_ASK, issue=issue)
            agent_rows.append(a)
            ask_rows.append(b)
            print(
                f"[r{i+1}] agent={a['agent_total_ms']:.0f}ms "
                f"tool={a['tool_ms']:.0f}ms llm_used={a['llm_used']} | "
                f"ask={b['ask_total_ms']:.0f}ms prep={b['ask_prepare_ms']:.0f}ms "
                f"ask_llm={b['ask_llm_ms']:.0f}ms"
            )

        rel = measure_agent_segments(con, Q_REL, use_llm=False, issue=issue)

        def col(rows, key):
            return [r[key] for r in rows]

        agent_total = col(agent_rows, "agent_total_ms")
        ask_total = col(ask_rows, "ask_total_ms")
        overhead = [a - b for a, b in zip(agent_total, ask_total)]

        out["agent_published"] = {
            "identity": _summary(col(agent_rows, "identity_ms")),
            "permission": _summary(col(agent_rows, "permission_ms")),
            "context": _summary(col(agent_rows, "context_ms")),
            "intent": _summary(col(agent_rows, "intent_ms")),
            "tool": _summary(col(agent_rows, "tool_ms")),
            "agent_total": _summary(agent_total),
            "llm_used_rate": sum(1 for r in agent_rows if r.get("llm_used")) / max(1, len(agent_rows)),
        }
        out["ask"] = {
            "prepare": _summary(col(ask_rows, "ask_prepare_ms")),
            "llm": _summary(col(ask_rows, "ask_llm_ms")),
            "ask_total": _summary(ask_total),
            "llm_used_rate": sum(1 for r in ask_rows if r.get("llm_used")) / max(1, len(ask_rows)),
        }
        out["overhead_agent_minus_ask"] = _summary(overhead)
        out["agent_relations_sample"] = {
            "agent_total_ms": round(rel["agent_total_ms"], 1),
            "tool_ms": round(rel["tool_ms"], 1),
            "intent": rel["intent"],
        }
        from app import embeddings
        from app.repro_selfcheck import environment_manifest

        out["environment_manifest"] = environment_manifest(
            embedding_calls=embeddings.call_count()
        )
        out["ok"] = True
    finally:
        con.close()

    path = ROOT / "eval" / "reports" / "AGENT_PERF.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    man = out.get("environment_manifest") or {}
    print(json.dumps({
        "ok": out.get("ok"),
        "agent_p50": out["agent_published"]["agent_total"]["p50_ms"],
        "agent_p95": out["agent_published"]["agent_total"]["p95_ms"],
        "ask_p50": out["ask"]["ask_total"]["p50_ms"],
        "ask_p95": out["ask"]["ask_total"]["p95_ms"],
        "overhead_p50": out["overhead_agent_minus_ask"]["p50_ms"],
        "overhead_p95": out["overhead_agent_minus_ask"]["p95_ms"],
        "manifest": {
            "commit": man.get("commit"),
            "model": man.get("model"),
            "vector": man.get("vector"),
            "embedding_calls": man.get("embedding_calls"),
        },
    }, ensure_ascii=False))
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
