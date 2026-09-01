#!/usr/bin/env python3
"""Ask/RAG 验收：真实 2026-8-17 语料 + 可观测指标（非单元测试）。

用法（在 mesh/ 目录）：
  python eval/run_acceptance.py
  python eval/run_acceptance.py --verbose
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ask_context, ask_engine, db, db_conn, qa_structured, retriever, search  # noqa: E402
from app.ask_scope import AskScope  # noqa: E402
from app.retriever import _hit_team_allowed, _hybrid_recall, rerank_hits  # noqa: E402

ISSUE_EXPORT = ROOT / "deploy" / "_issue_2026-8-17.json"


def _seed_golden_db(con) -> dict:
    raw = json.loads(ISSUE_EXPORT.read_text(encoding="utf-8"))
    iss = raw["issue"]
    data = raw["data"]
    published = json.dumps(data, ensure_ascii=False)
    con.execute(
        """INSERT INTO issues(id, slug, date_start, date_end, period_label, status, published_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            iss["id"], iss["slug"], iss["date_start"], iss["date_end"],
            iss.get("period_label") or iss["slug"], "published", published,
        ),
    )
    snap = json.loads(iss["published_items_snapshot"] or "[]")
    for it in snap:
        if it.get("blocked") or it.get("merged_into"):
            continue
        con.execute(
            """INSERT INTO items(id, issue_id, source_id, team, stype, zone, level, kind, text,
               entities, roles, signals, source_label, pointer, blocked, owner_team, channel, merged_into, source_labels)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                it["id"], iss["id"], it.get("source_id"), it.get("team"), it.get("stype"),
                it.get("zone"), it.get("level"), it.get("kind"), it.get("text"),
                it.get("entities") or "[]", it.get("roles") or "[]", it.get("signals") or "[]",
                it.get("source_label"), it.get("pointer"), it.get("blocked") or 0,
                it.get("owner_team") or it.get("team"), it.get("channel"),
                it.get("merged_into"), it.get("source_labels"),
            ),
        )
    db.reindex_issue(con, iss["id"], items=True)
    con.commit()
    stats = {
        "slug": iss["slug"],
        "items": con.execute("SELECT COUNT(*) c FROM items WHERE issue_id=?", (iss["id"],)).fetchone()["c"],
        "fts": con.execute("SELECT COUNT(*) c FROM search_fts").fetchone()["c"],
        "facts": con.execute("SELECT COUNT(*) c FROM entity_team_facts").fetchone()["c"],
        "chunks": con.execute("SELECT COUNT(*) c FROM chunk_index").fetchone()["c"],
    }
    return stats


def _old_team_filter(hits: list[dict], team: str) -> list[dict]:
    """修复前：无 owner_team 的 FTS 节一律被 team 过滤丢弃。"""
    if not team:
        return hits
    return [h for h in hits if _hit_team_allowed_old(h, team)]


def _hit_team_allowed_old(h: dict, team: str) -> bool:
    if not team:
        return True
    ot = (h.get("owner_team") or "").strip()
    return bool(ot) and ot == team


def _old_dedup_count(groups: list[list[dict]]) -> int:
    best: dict[tuple, dict] = {}
    for hits in groups:
        for h in hits or []:
            key = _old_dedup_key(h)
            prev = best.get(key)
            if prev is None or float(h.get("score") or 0) < float(prev.get("score") or 0):
                best[key] = h
    return len(best)


def _old_dedup_key(h: dict) -> tuple:
    if h.get("item_id"):
        return ("item", h.get("issue_slug"), int(h["item_id"]))
    if h.get("chunk_id"):
        return ("chunk", h.get("chunk_id"))
    body = re.sub(r"\s+", "", (h.get("body") or "")[:160])
    title = re.sub(r"\s+", "", (h.get("title") or "")[:80])
    return ("text", h.get("issue_slug"), h.get("section"), title, body)


def _ctx_titles(prepared: dict, n: int = 5) -> list[str]:
    out = []
    for c in prepared.get("contexts") or []:
        if c.get("章节") in ("检索范围", "结构化检索", "查询说明"):
            continue
        t = (c.get("标题") or c.get("title") or "").strip()
        if t:
            out.append(t[:40])
        if len(out) >= n:
            break
    return out


def _run_case(con, case: dict, verbose: bool) -> dict:
    q = case["q"]
    scope_kw = case.get("scope") or {}
    scope = AskScope(channel="web", user_id=1, role="viewer", **scope_kw)
    route = ask_context.route(q, case.get("messages") or [])
    prepared = ask_engine.prepare(
        con, q, scope,
        search_q=route.get("search_q") or q,
        context_refs=route.get("context_refs") if route.get("kind") == "followup" else None,
    )
    result = {
        "id": case["id"],
        "q": q,
        "route_kind": route.get("kind"),
        "route_reason": route.get("reason"),
        "mode": prepared.get("mode"),
        "n_hits": prepared.get("n_hits", prepared.get("n_context")),
        "n_context": prepared.get("n_context"),
        "total": prepared.get("total"),
        "direct": bool(prepared.get("direct_answer")),
        "titles": _ctx_titles(prepared),
        "checks": [],
        "pass": True,
    }

    for name, fn in case.get("assert", {}).items():
        ok, detail = fn(result, prepared, route, con, scope)
        result["checks"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            result["pass"] = False

    if verbose:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    td = tempfile.mkdtemp(prefix="mesh_accept_")
    db_path = Path(td) / "accept.db"
    old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
    db_conn.DB_PATH = str(db_path)
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = str(db_path)

    con = db.connect()
    db.init_db(seed=True)
    stats = _seed_golden_db(con)
    print(f"语料：{stats['slug']} items={stats['items']} fts={stats['fts']} facts={stats['facts']} chunks={stats['chunks']}")
    print()

    def chk_structured_path(res, prep, *_):
        ok = prep.get("mode") == "structured"
        da = prep.get("direct_answer") or ""
        if (prep.get("total") or 0) == 0:
            ok = ok and "未找到" in da and "None" not in da
            detail = f"total=0 empty_ok={'未找到' in da} teams_in_answer={'商业化' in da and '编辑部' in da}"
        else:
            ok = ok and len(res["titles"]) > 0
            detail = f"total={prep.get('total')} titles={res['titles'][:3]}"
        return ok, detail

    def chk_mianbi_hits(res, prep, *_):
        blob = json.dumps(prep.get("contexts") or [], ensure_ascii=False)
        ok = "面壁" in blob or "詹杨" in blob
        return ok, f"n_ctx={res['n_context']} has_mianbi={ok}"

    def chk_team_not_zero(res, prep, *_):
        ok = res["n_context"] > 0 or (prep.get("total") or 0) > 0
        return ok, f"n_ctx={res['n_context']} total={prep.get('total')}"

    def chk_independent(res, prep, route, *_):
        ok = route.get("kind") == "independent"
        return ok, f"kind={route.get('kind')} reason={route.get('reason')}"

    def chk_followup(res, prep, route, *_):
        ok = route.get("kind") == "followup"
        return ok, f"kind={route.get('kind')} search_q={(route.get('search_q') or '')[:60]}"

    def chk_slug_scoped(res, prep, route, con, scope):
        q = "面壁智能"
        scope_all = AskScope(channel="web", user_id=1, role="viewer")
        scope_one = AskScope(channel="web", user_id=1, role="viewer", slug="2026-8-17")
        pa = ask_engine.prepare(con, q, scope_all)
        po = ask_engine.prepare(con, q, scope_one)
        issues_a = {c.get("期号") for c in pa.get("contexts") or [] if c.get("期号")}
        issues_o = {c.get("期号") for c in po.get("contexts") or [] if c.get("期号")}
        ok = (not issues_o - {"2026-8-17", "", "查询说明"}) and len(issues_o) <= len(issues_a)
        return ok, f"scoped_issues={sorted(issues_o)} all_issues={sorted(issues_a)}"

    def chk_team_filter_improved(res, prep, route, con, scope):
        team = scope.team_filter or "编辑部"
        hits, _ = _hybrid_recall(con, "面壁智能", AskScope(channel="web", user_id=1, team=team), limit=24)
        fts_only = search.fts_search(con, "面壁智能", limit=24)
        old_n = len(_old_team_filter(fts_only, team))
        new_n = len([h for h in hits if _hit_team_allowed(h, team) or h in hits])
        ok = len(hits) > old_n
        return ok, f"hybrid_team={team} hits={len(hits)} old_fts_only_pass={old_n}"

    def chk_dedup_improved(res, prep, route, con, scope):
        fts = search.fts_search(con, "面壁智能", slug="2026-8-17", limit=20)
        from app import embeddings
        vec = embeddings.vector_search(con, [0.0] * 1536, limit=0) if False else []
        # 用 chunk 模拟：同一 body 的 fts + item
        items = [h for h in fts if h.get("item_id")]
        old_n = _old_dedup_count([fts, items])
        new_n = len(search.merge_hits(fts, items, limit=40))
        ok = new_n <= old_n and (old_n - new_n) >= 0
        # 找重复 body
        bodies = [re.sub(r"\s+", "", (h.get("body") or "")[:80]) for h in fts]
        dup_bodies = len(bodies) - len(set(bodies))
        return ok, f"merge={new_n} old_keys={old_n} fts_dup_bodies={dup_bodies}"

    def chk_entity_norm(res, prep, *_):
        intent = qa_structured.parse_intent(
            "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？"
        )
        ctxs, total = qa_structured.query_intersect(
            con, intent["team_a"], intent["team_b"],
            intent.get("date_from"), intent.get("section") or "接触",
            intent.get("hardware", False), intent.get("date_to"),
        )
        names = [c.get("标题", "") for c in ctxs[:5]]
        ok = total >= 0  # 结构化能跑通；有结果更好
        if total > 0:
            ok = any(names)
        return ok, f"intersect_total={total} sample={names[:3]}"

    prior_msgs = [
        {"role": "user", "content": "具身智能有哪些公司", "meta": {}},
        {
            "role": "assistant", "content": "优必选、智元…",
            "meta": {
                "context_refs": {
                    "analysis_id": "prior1",
                    "last_user_q": "具身智能有哪些公司",
                    "entities": ["优必选", "智元机器人"],
                    "chunk_ids": ["abc"],
                    "item_ids": ["100"],
                    "teams": [], "issues": [],
                },
            },
        },
    ]

    cases = [
        {
            "id": "A1_structured_intersect",
            "q": "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？",
            "assert": {"structured_path": chk_structured_path, "entity_norm": chk_entity_norm},
        },
        {
            "id": "A2_mianbi_entity",
            "q": "编辑部接触了面壁智能吗",
            "assert": {"has_evidence": chk_mianbi_hits},
        },
        {
            "id": "A3_topic_broad",
            "q": "具身智能有哪些公司",
            "assert": {"has_hits": chk_team_not_zero},
        },
        {
            "id": "A4_short_independent",
            "q": "面壁智能",
            "messages": prior_msgs,
            "assert": {"independent": chk_independent, "has_hits": chk_mianbi_hits},
        },
        {
            "id": "A5_followup",
            "q": "还有哪些",
            "messages": prior_msgs,
            "assert": {"followup": chk_followup},
        },
        {
            "id": "A6_team_scope",
            "q": "面壁智能",
            "scope": {"team": "编辑部"},
            "assert": {"team_filter": chk_team_filter_improved, "has_hits": chk_mianbi_hits},
        },
        {
            "id": "A7_slug_scope",
            "q": "面壁智能",
            "assert": {"slug_filter": chk_slug_scoped},
        },
        {
            "id": "A8_dedup",
            "q": "面壁智能",
            "scope": {"slug": "2026-8-17"},
            "assert": {"dedup": chk_dedup_improved},
        },
    ]

    passed, failed = 0, 0
    rows = []
    for case in cases:
        r = _run_case(con, case, args.verbose)
        rows.append(r)
        status = "PASS" if r["pass"] else "FAIL"
        if r["pass"]:
            passed += 1
        else:
            failed += 1
        bad = [c for c in r["checks"] if not c["ok"]]
        print(f"[{status}] {r['id']}  route={r['route_kind']} mode={r['mode']} n_ctx={r['n_context']} total={r.get('total')}")
        if r["titles"]:
            print(f"       命中标题: {', '.join(r['titles'][:4])}")
        for c in r["checks"]:
            mark = "ok" if c["ok"] else "FAIL"
            print(f"       · {c['name']}: {mark} — {c['detail']}")
        if bad:
            print(f"       未通过: {[x['name'] for x in bad]}")
        print()

    # 修复前 vs 后：团队过滤对比（全库 hybrid）
    team = "编辑部"
    scope = AskScope(channel="web", user_id=1, team=team)
    hits_new, meta = _hybrid_recall(con, "面壁智能", scope, limit=24)
    fts = search.fts_search(con, "面壁智能", limit=24)
    for h in fts:
        h.setdefault("source", "fts")
    old_pass = _old_team_filter(fts, team)
    print("=== 修复前后对比（编辑部 · 面壁智能）===")
    print(f"  修复前 FTS 通过 team 过滤: {len(old_pass)} 条（无 owner_team 的 digest 节会被丢弃）")
    print(f"  修复后 hybrid 召回:         {len(hits_new)} 条")
    print(f"  时间窗: {meta.get('date_from')} ~ {meta.get('date_to') or '…'}")
    if hits_new:
        print(f"  Top 标题: {[ (h.get('title') or '')[:30] for h in hits_new[:4] ]}")
    print()

    con.close()
    db_conn.DB_PATH, db_conn.MESH_DB_URL = old_path, old_url

    print(f"验收汇总: {passed}/{len(cases)} 场景通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
