#!/usr/bin/env python3
"""Ask 评估基线：路由 + 检索 smoke（不调 LLM）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import ask_context, db, ask_engine  # noqa: E402
from app.ask_scope import AskScope  # noqa: E402

EVAL = Path(__file__).resolve().parent / "ask_eval_v1.jsonl"


def main() -> int:
    rows = []
    for line in EVAL.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    con = db.connect()
    db.migrate(con)
    scope = AskScope(channel="web", user_id=1, role="viewer")
    ok = 0
    for row in rows:
        q = row["q"]
        msgs = []
        if row.get("needs_prior"):
            msgs = [
                {"role": "user", "content": "具身智能有哪些公司", "meta": {}},
                {
                    "role": "assistant",
                    "content": "…",
                    "meta": {
                        "context_refs": {
                            "analysis_id": "eval-prior",
                            "last_user_q": "具身智能有哪些公司",
                            "entities": ["优必选", "面壁智能"],
                            "chunk_ids": ["c1"],
                            "teams": [],
                            "issues": [],
                            "item_ids": [],
                        },
                    },
                },
            ]
        route = ask_context.route(q, msgs)
        kind = route["kind"]
        if row.get("expect_mode") == "independent" and kind != "independent":
            print(f"FAIL {row['id']} route={kind}")
            continue
        if row.get("expect_mode") == "followup" and kind != "followup":
            print(f"FAIL {row['id']} route={kind}")
            continue
        prepared = ask_engine.prepare(con, q, scope, search_q=route.get("search_q") or q)
        mode = prepared.get("mode") or ""
        print(f"OK {row['id']} route={kind} mode={mode} n_ctx={prepared.get('n_context', 0)}")
        ok += 1
    con.close()
    print(f"pass {ok}/{len(rows)}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
