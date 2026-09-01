#!/usr/bin/env python3
"""端到端 Ask 验收：真实语料 + 真实 LLM（少量黄金问句）。"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))

from app import ask_analysis, ask_engine, db, db_conn  # noqa: E402
from app.ask_scope import AskScope  # noqa: E402
from eval.run_acceptance import _seed_golden_db  # noqa: E402


E2E_CASES = [
    {
        "id": "E1_mianbi",
        "q": "编辑部接触了面壁智能吗",
        "must_contain": ["面壁", "编辑部"],
        "must_not": ["已校验断言", "根据已校验"],
    },
    {
        "id": "E2_short_entity",
        "q": "面壁智能",
        "must_contain": ["面壁"],
        "must_not": ["已校验断言"],
    },
]


def main() -> int:
    td = tempfile.mkdtemp(prefix="mesh_e2e_")
    db_path = Path(td) / "e2e.db"
    old_path, old_url = db_conn.DB_PATH, db_conn.MESH_DB_URL
    db_conn.DB_PATH = str(db_path)
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = str(db_path)

    con = db.connect()
    db.init_db(seed=True)
    stats = _seed_golden_db(con)
    con.close()
    print(f"语料就绪: {stats['slug']} fts={stats['fts']} facts={stats['facts']}\n")

    scope = AskScope(channel="web", user_id=1, role="viewer")
    passed = 0
    for case in E2E_CASES:
        q = case["q"]
        print(f"=== {case['id']} ===")
        print(f"Q: {q}")
        t0 = time.time()
        con = db.connect()
        try:
            prepared = ask_engine.prepare(con, q, scope)
            print(f"  retrieve: mode={prepared.get('mode')} n_ctx={prepared.get('n_context')} latency={prepared.get('latency_ms')}ms")
            if prepared.get("direct_answer"):
                ans = prepared["direct_answer"]
                steps, meta = [], {"usage": {"path": "direct"}}
            else:
                ans, steps, meta = ask_analysis.run_analysis_collect(q, prepared, history=[])
            elapsed = int((time.time() - t0) * 1000)
        finally:
            con.close()

        usage = meta.get("usage") or {}
        verify = meta.get("verify") or {}
        print(f"  analysis: path={usage.get('path')} llm_calls={usage.get('llm_calls')} elapsed={elapsed}ms")
        print(f"  verify: keep={verify.get('verified')} reject={verify.get('rejected')} down={verify.get('downgraded')}")
        print(f"  steps: {[s.get('step')+':'+s.get('status') for s in steps if s.get('type')=='step']}")
        print(f"  A: {(ans or '')[:400]}")
        if len(ans or "") > 400:
            print("     …")

        ok = True
        for w in case.get("must_contain") or []:
            if w not in (ans or ""):
                ok = False
                print(f"  FAIL missing: {w}")
        for w in case.get("must_not") or []:
            if w in (ans or ""):
                ok = False
                print(f"  FAIL forbidden: {w}")
        if ok:
            passed += 1
            print("  => PASS\n")
        else:
            print("  => FAIL\n")

    db_conn.DB_PATH, db_conn.MESH_DB_URL = old_path, old_url
    print(f"E2E: {passed}/{len(E2E_CASES)}")
    return 0 if passed == len(E2E_CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
