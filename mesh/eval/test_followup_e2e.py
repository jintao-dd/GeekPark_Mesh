#!/usr/bin/env python3
"""二轮 follow-up E2E：Q1 → context_refs → Q2「还有哪些」。"""
from __future__ import annotations

import json
import time

from app import ask_analysis, ask_context, ask_engine, ask_store, conversation, db
from app.ask_scope import AskScope


def run_followup_e2e(con) -> dict:
    scope = AskScope(channel="web", user_id=1, role="viewer")
    user = {"id": 1, "username": "admin", "role": "admin", "team": ""}
    sess = conversation.ensure_session(con, scope, user)
    con.commit()

    q1 = "具身智能有哪些公司"
    leak_marker = "EVAL_LEAK_具身答案禁止进入Q2检索"
    route1 = ask_context.route(q1, [])
    prep1 = ask_engine.prepare(con, q1, scope, search_q=route1.get("search_q") or q1)
    ans1, steps1, meta1 = ask_analysis.run_analysis_collect(
        q1, prep1, history=[],
        persist={"session_id": sess["id"], "user_id": user["id"]},
    )
    refs1 = meta1.get("context_refs") or {}
    conversation.append_turn(
        con, sess["id"], user_text=q1, assistant_text=leak_marker,
        mode=prep1.get("mode", ""), n_context=prep1.get("n_context", 0),
        meta={"analysis_id": meta1.get("analysis_id"), "context_refs": refs1},
    )
    con.commit()

    msgs = conversation.recent_messages(con, sess["id"], limit=6)
    q2 = "还有哪些"
    route2 = ask_context.route(q2, msgs)
    prep2 = ask_engine.prepare(
        con, q2, scope,
        search_q=route2.get("search_q") or q2,
        context_refs=route2.get("context_refs") if route2.get("kind") == "followup" else None,
    )

    checks = []
    ok = True

    def chk(name, passed, detail):
        nonlocal ok
        checks.append({"check": name, "ok": passed, "detail": detail})
        if not passed:
            ok = False

    chk("q2_followup", route2.get("kind") == "followup", f"kind={route2.get('kind')}")
    sq = route2.get("search_q") or ""
    chk("no_q1_answer_in_search_q", leak_marker not in sq and ans1[:40] not in sq, f"search_q={sq[:100]}")
    chk("refs_inherited", bool((route2.get("context_refs") or {}).get("last_user_q")), "last_user_q set")
    chk("q2_re_retrieve", (prep2.get("n_context") or 0) > 0, f"n_ctx={prep2.get('n_context')}")
    inherited_entities = (route2.get("context_refs") or {}).get("entities") or []
    chk("entities_from_refs", len(inherited_entities) > 0, f"entities={inherited_entities[:4]}")

    ans2, _, meta2 = ask_analysis.run_analysis_collect(
        q2, prep2, history=[],
        persist={"session_id": sess["id"], "user_id": user["id"]},
    )
    chk("q2_new_analysis_id", meta2.get("analysis_id") != meta1.get("analysis_id"), "new analysis")
    chk("no_leak_in_q2_answer", leak_marker not in (ans2 or ""), "answer clean")

    return {
        "pass": ok,
        "summary": "二轮 follow-up OK" if ok else "二轮 follow-up 存在失败检查",
        "q1": {"analysis_id": meta1.get("analysis_id"), "refs_entities": refs1.get("entities", [])[:6]},
        "q2": {
            "routing": {"kind": route2.get("kind"), "search_q": sq[:120]},
            "recall": {"n_context": prep2.get("n_context"), "titles": [
                (c.get("标题") or "")[:30] for c in (prep2.get("contexts") or [])[:4]
            ]},
            "analysis_id": meta2.get("analysis_id"),
        },
        "checks": checks,
    }


if __name__ == "__main__":
    from eval.run_acceptance import _seed_golden_db
    import tempfile
    from pathlib import Path
    from app import db_conn

    td = tempfile.mkdtemp()
    db_conn.DB_PATH = str(Path(td) / "t.db")
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = db_conn.DB_PATH
    con = db.connect()
    db.init_db(seed=True)
    _seed_golden_db(con)
    print(json.dumps(run_followup_e2e(con), ensure_ascii=False, indent=2))
