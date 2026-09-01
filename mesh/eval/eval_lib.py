"""Eval 共享：指标采集、断言、报告结构。"""
from __future__ import annotations

import json
import re
import time
from typing import Any

from app import ask_analysis, ask_context, ask_engine, ask_store, qa_structured, retriever, search
from app.ask_scope import AskScope
from app.retriever import _hit_team_allowed, _hybrid_recall


def load_cases(path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def prior_messages(case: dict) -> list[dict]:
    if not case.get("needs_prior"):
        return []
    pq = case.get("prior_q") or "上一问"
    leak = case.get("prior_answer_leak") or "SECRET_PRIOR_ANSWER不应进入检索"
    entities = case.get("prior_entities") or []
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
                    "teams": [], "issues": [],
                },
            },
        },
    ]


def db_stats(con) -> dict:
    def c(sql: str) -> int:
        try:
            return int(con.execute(sql).fetchone()["c"])
        except Exception:
            return -1

    issues = [
        r["slug"]
        for r in con.execute(
            "SELECT slug FROM issues WHERE status='published' ORDER BY date_end DESC"
        ).fetchall()
    ]
    return {
        "issues_published": len(issues),
        "issue_slugs": issues[:12],
        "items": c("SELECT COUNT(*) c FROM items WHERE blocked=0"),
        "fts": c("SELECT COUNT(*) c FROM search_fts"),
        "facts": c("SELECT COUNT(*) c FROM entity_team_facts"),
        "chunks": c("SELECT COUNT(*) c FROM chunk_index"),
    }


def context_titles(prepared: dict, n: int = 6) -> list[str]:
    out = []
    for ctx in prepared.get("contexts") or []:
        if ctx.get("章节") in ("检索范围", "结构化检索", "查询说明") or ctx.get("期号") == "查询说明":
            continue
        t = (ctx.get("标题") or ctx.get("title") or "").strip()
        if t:
            out.append(t[:48])
        if len(out) >= n:
            break
    return out


def context_issues(prepared: dict) -> list[str]:
    s = set()
    for ctx in prepared.get("contexts") or []:
        iss = (ctx.get("期号") or "").strip()
        if iss and iss not in ("查询说明", ""):
            s.add(iss)
    return sorted(s)


_ROUTING_CHECKS = frozenset({"routing", "no_answer_in_search_q"})
_RETRIEVAL_CHECKS = frozenset({
    "mode", "direct_answer", "max_hits", "min_context", "min_total", "scope_issues",
})
_RETRIEVAL_PREFIX = "context_has_"
_EVIDENCE_CHECKS = frozenset({"unsupported_claims_bounded"})
_ANSWER_PREFIXES = ("answer_has_", "answer_forbid_", "no_markdown_headers")


def classify_failure_layer(checks: list[dict]) -> str | None:
    """将失败断言归类：routing | retrieval | evidence | answer。"""
    failed = [c.get("check") or "" for c in checks if not c.get("ok")]
    if not failed:
        return None
    for name in failed:
        if name in _ROUTING_CHECKS:
            return "routing"
    for name in failed:
        if name in _RETRIEVAL_CHECKS or name.startswith(_RETRIEVAL_PREFIX):
            return "retrieval"
    for name in failed:
        if name in _EVIDENCE_CHECKS:
            return "evidence"
    for name in failed:
        if any(name.startswith(p) for p in _ANSWER_PREFIXES):
            return "answer"
    return "other"


def summarize_failures_by_layer(questions: list[dict]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {
        "routing": [], "retrieval": [], "evidence": [], "answer": [], "other": [],
    }
    for r in questions:
        if r.get("pass"):
            continue
        layer = r.get("failure_layer") or classify_failure_layer(r.get("checks") or [])
        key = layer if layer in out else "other"
        bad = [c["check"] for c in (r.get("checks") or []) if not c.get("ok")]
        out[key].append(f"{r['id']}:{bad}")
    return out


def team_filter_baseline(con, q: str, team: str) -> dict:
    fts = search.fts_search(con, q, limit=24)
    for h in fts:
        h.setdefault("source", "fts")
    old_pass = [h for h in fts if (h.get("owner_team") or "").strip() == team]
    hits, meta = _hybrid_recall(con, q, AskScope(channel="web", user_id=1, team=team), limit=24)
    return {
        "old_fts_with_owner_team": len(old_pass),
        "fts_total": len(fts),
        "new_hybrid": len(hits),
        "date_from": meta.get("date_from"),
        "date_to": meta.get("date_to"),
    }


def evaluate_retrieval(con, case: dict) -> dict[str, Any]:
    q = case["q"]
    scope_kw = dict(case.get("scope") or {})
    scope = AskScope(channel="web", user_id=1, role="viewer", **scope_kw)
    msgs = prior_messages(case)
    t0 = time.time()
    route = ask_context.route(q, msgs)
    prepared = ask_engine.prepare(
        con, q, scope,
        search_q=route.get("search_q") or q,
        context_refs=route.get("context_refs") if route.get("kind") == "followup" else None,
    )
    latency = int((time.time() - t0) * 1000)
    titles = context_titles(prepared)
    issues = context_issues(prepared)
    blob = json.dumps(prepared.get("contexts") or [], ensure_ascii=False)

    checks: list[dict] = []
    ok = True

    def add(name: str, passed: bool, detail: str):
        nonlocal ok
        checks.append({"check": name, "ok": passed, "detail": detail})
        if not passed:
            ok = False

    er = case.get("expect_route")
    if er:
        add("routing", route.get("kind") == er, f"kind={route.get('kind')} reason={route.get('reason')}")

    if case.get("needs_prior") and route.get("kind") == "followup":
        leak = (case.get("prior_answer_leak") or "").strip()
        sq = route.get("search_q") or ""
        if leak:
            add("no_answer_in_search_q", leak not in sq, f"search_q={sq[:80]}")

    em = case.get("expect_mode")
    if em:
        add("mode", (prepared.get("mode") or "").startswith(em), f"mode={prepared.get('mode')}")

    eso = case.get("expect_set_op")
    if eso:
        rp = prepared.get("retrieval_plan") or {}
        got = rp.get("set_op") or "none"
        add("set_op", got == eso, f"set_op={got} expect={eso}")

    if case.get("expect_direct"):
        add("direct_answer", prepared.get("direct_answer") is not None, "direct=" + str(bool(prepared.get("direct_answer"))))

    mx = case.get("expect_max_hits")
    if mx is not None:
        nh = int(prepared.get("n_hits", prepared.get("n_context")) or 0)
        add("max_hits", nh <= int(mx), f"n_hits={nh} max={mx}")

    mc = case.get("min_context")
    if mc is not None:
        add("min_context", (prepared.get("n_context") or 0) >= mc, f"n_ctx={prepared.get('n_context')}")

    mt = case.get("min_total")
    if mt is not None:
        add("min_total", (prepared.get("total") or 0) >= mt, f"total={prepared.get('total')}")

    for kw in case.get("must_context") or []:
        add(f"context_has_{kw}", kw in blob, f"found={kw in blob}")

    si = case.get("scope_issues")
    if si and prepared.get("n_context", 0) > 0:
        add("scope_issues", set(issues).issubset(set(si)), f"issues={issues} expect={si}")

    if scope_kw.get("team"):
        bl = team_filter_baseline(con, q, scope.team_filter or scope_kw["team"])
    else:
        bl = {}

    failure_layer = classify_failure_layer(checks) if not ok else None

    return {
        "id": case["id"],
        "q": q,
        "tags": case.get("tags") or [],
        "pass": ok,
        "failure_layer": failure_layer,
        "routing": {
            "kind": route.get("kind"),
            "reason": route.get("reason"),
            "search_q": (route.get("search_q") or "")[:120],
            "parent_analysis_id": route.get("parent_analysis_id"),
        },
        "recall": {
            "mode": prepared.get("mode"),
            "n_hits": prepared.get("n_hits", prepared.get("n_context")),
            "n_context": prepared.get("n_context"),
            "total": prepared.get("total"),
            "date_from": prepared.get("date_from"),
            "date_to": prepared.get("date_to"),
            "latency_ms": latency,
            "titles": titles,
            "issues": issues,
            "direct_answer": bool(prepared.get("direct_answer")),
        },
        "rerank": bl,
        "checks": checks,
        "prepared": prepared,
        "route": route,
    }


def evaluate_e2e(con, case: dict, row: dict, *, persist: dict | None = None) -> dict[str, Any]:
    prepared = row["prepared"]
    q = case["q"]
    retrieve_ms = int((row.get("recall") or {}).get("latency_ms") or 0)
    if prepared.get("direct_answer") is not None:
        ans = prepared["direct_answer"]
        meta = {"usage": {"path": "direct"}, "verify": {}}
        steps = []
        analysis_ms = 0
    else:
        t0 = time.time()
        ans, steps, meta = ask_analysis.run_analysis_collect(
            q, prepared, history=[], persist=persist or {},
        )
        analysis_ms = int((time.time() - t0) * 1000)
        meta.setdefault("usage", {})["analysis_ms"] = analysis_ms

    verify = meta.get("verify") or {}
    usage = dict(meta.get("usage") or {})
    usage["retrieve_ms"] = retrieve_ms
    usage["analysis_ms"] = analysis_ms
    usage["total_ms"] = retrieve_ms + analysis_ms
    checks = list(row.get("checks") or [])
    ok = row.get("pass", True)

    def add(name: str, passed: bool, detail: str):
        nonlocal ok
        checks.append({"check": name, "ok": passed, "detail": detail})
        if not passed:
            ok = False

    for w in case.get("must_answer") or []:
        add(f"answer_has_{w}", w in (ans or ""), f"len={len(ans or '')}")
    for w in case.get("forbid_answer") or []:
        add(f"answer_forbid_{w}", w not in (ans or ""), f"hit={w in (ans or '')}")
    if case.get("forbid_answer") and any(x in ("#", "##") for x in case.get("forbid_answer") or []):
        md = bool(re.search(r"(^|\n)\s*#{1,6}\s", ans or ""))
        add("no_markdown_headers", not md, f"md_headers={md}")

    unsupported = int(verify.get("rejected") or 0)
    add("unsupported_claims_bounded", unsupported <= 3, f"rejected={unsupported}")

    failure_layer = classify_failure_layer(checks) if not ok else None

    return {
        **row,
        "pass": ok,
        "failure_layer": failure_layer,
        "checks": checks,
        "evidence": {
            "analysis_id": meta.get("analysis_id"),
            "sources": meta.get("sources") or [],
            "context_refs": meta.get("context_refs") or {},
            "steps_n": len(steps),
        },
        "answer": {
            "text": (ans or "")[:800],
            "len": len(ans or ""),
            "correctness": "pass" if ok else "fail",
        },
        "verify": verify,
        "latency": {
            "retrieve_ms": retrieve_ms,
            "analysis_ms": analysis_ms,
            "total_ms": retrieve_ms + analysis_ms,
        },
        "usage": usage,
    }
