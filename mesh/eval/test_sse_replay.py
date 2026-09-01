#!/usr/bin/env python3
"""SSE 断线回放（Phase 1 验收标准）。

Blocking（必须通过）：
- analysis_id 可获取
- token 前主动断开
- ask_store：answer 持久化、context_refs、sources、evidence_refs
- status=completed
- GET /api/ask/analysis/{id} 可回放

不验证：token prefix 与二次 LLM 输出一致（非确定性）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _sources_have_evidence(sources: list) -> bool:
    for s in sources or []:
        if not isinstance(s, dict):
            continue
        if s.get("evidence"):
            return True
        for f in s.get("facts") or []:
            if isinstance(f, dict) and f.get("evidence_refs"):
                return True
    return False


def run_sse_replay_test(con=None, *, restore_db=None) -> dict:
    os.environ["MESH_ENV"] = "development"
    os.environ["MESH_BASE_URL"] = "http://localhost:8080"
    os.environ.setdefault("MESH_SECRET", "eval-sse-test-secret-32chars-min!")
    os.environ.pop("MESH_DB_URL", None)

    from starlette.testclient import TestClient
    from app import ask_store, auth, db, db_conn

    os.environ["MESH_DB"] = db_conn.DB_PATH
    con = con or db.connect()

    user_row = con.execute(
        "SELECT id, username, role, team, display FROM users WHERE username='admin'"
    ).fetchone() or con.execute(
        "SELECT id, username, role, team, display FROM users LIMIT 1"
    ).fetchone()
    user = dict(user_row)
    cookie_val = auth.make_session({
        "username": user["username"], "role": user["role"],
        "team": user.get("team") or "", "display": user.get("display") or user["username"],
    })

    from app.main import app
    client = TestClient(app)

    q = "编辑部接触了面壁智能吗"
    analysis_id = None
    events_b = []
    disconnected = False
    phase = "none"

    with client.stream(
        "POST",
        "/api/ask/stream",
        json={"q": q, "analysis": True},
        cookies={auth.COOKIE: cookie_val},
    ) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            ev = json.loads(line[6:])
            events_b.append(ev.get("type") or ev.get("step"))
            if ev.get("analysis_id"):
                analysis_id = ev.get("analysis_id")
            if ev.get("type") == "step" and ev.get("step") == "route":
                analysis_id = ev.get("analysis_id") or analysis_id
            if ev.get("type") == "token":
                disconnected = True
                phase = "before_first_token"
                break

    checks = []
    ok = True

    def chk(name, passed, detail, *, blocking: bool = True):
        nonlocal ok
        checks.append({"check": name, "ok": passed, "detail": detail, "blocking": blocking})
        if blocking and not passed:
            ok = False

    chk("disconnected_at_token", disconnected, f"phase={phase} events={events_b}")
    chk("analysis_id", bool(analysis_id), str(analysis_id))

    row = None
    for _ in range(30):
        row = ask_store.get(con, analysis_id) if analysis_id else None
        if row and row.get("answer") and row.get("status") == "completed":
            break
        time.sleep(2)

    chk("store_row", bool(row), "ask_analyses")
    if row:
        chk("store_answer", len(row.get("answer") or "") > 20, f"len={len(row.get('answer') or '')}")
        refs = row.get("context_refs") or {}
        chk("store_context_refs", bool(refs.get("analysis_id")), f"refs_keys={list(refs.keys())[:6]}")
        sources = row.get("sources") or []
        chk("store_sources", len(sources) > 0, f"n_sources={len(sources)}")
        chk("store_evidence_refs", _sources_have_evidence(sources), f"evidence_in_sources={bool(sources)}")
        chk("complete_state", row.get("status") == "completed", f"status={row.get('status')}")

        replay = client.get(
            f"/api/ask/analysis/{analysis_id}",
            cookies={auth.COOKIE: cookie_val},
        )
        chk("api_replay", replay.status_code == 200, f"http={replay.status_code}")
        if replay.status_code == 200:
            body = replay.json()
            chk(
                "api_replay_answer",
                len(body.get("answer") or "") > 20,
                f"len={len(body.get('answer') or '')}",
            )
            same = (body.get("answer") or "") == (row.get("answer") or "")
            chk("api_replay_stable", same, f"store_len={len(row.get('answer') or '')}")

    return {
        "pass": ok,
        "summary": "SSE 回放 OK" if ok else "SSE 回放未完全通过",
        "analysis_id": analysis_id,
        "disconnect_phase": phase,
        "events_seen": events_b,
        "store": {
            "status": (row or {}).get("status"),
            "answer_len": len((row or {}).get("answer") or ""),
            "n_sources": len((row or {}).get("sources") or []),
        },
        "checks": checks,
        "criteria": "Phase1: persist+replay; no token prefix match required",
    }


if __name__ == "__main__":
    import sys
    import tempfile
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import db, db_conn
    from eval.run_acceptance import _seed_golden_db

    td = tempfile.mkdtemp()
    db_conn.DB_PATH = str(Path(td) / "t.db")
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = db_conn.DB_PATH
    con = db.connect()
    db.init_db(seed=True)
    _seed_golden_db(con)
    print(json.dumps(run_sse_replay_test(con), ensure_ascii=False, indent=2))
