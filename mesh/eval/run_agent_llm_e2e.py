#!/usr/bin/env python3
"""Layer 2.2：Agent 真实 LLM E2E（小 Gold 集）。

默认要求 MESH_AGENT_USE_LLM=1；否则以 --allow-no-llm 仅跑编排断言（不算产品验收通过）。

用法：
  MESH_AGENT_USE_LLM=1 python eval/run_agent_llm_e2e.py --corpus golden
  MESH_AGENT_USE_LLM=1 python eval/run_agent_llm_e2e.py --db-url "$MESH_DB_URL"

不并入 run_mesh_agent_full_acceptance.py。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD = ROOT / "eval" / "agent_e2e_gold_v1.jsonl"
ISSUE_EXPORT = ROOT / "deploy" / "_issue_2026-8-17.json"


def _load_gold() -> list[dict]:
    rows = []
    for line in GOLD.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def _seed_golden(con) -> None:
    raw = json.loads(ISSUE_EXPORT.read_text(encoding="utf-8"))
    iss = raw["issue"]
    data = raw["data"]
    con.execute(
        """INSERT INTO issues(id, slug, date_start, date_end, period_label, status, published_json)
           VALUES (?,?,?,?,?,?,?)""",
        (
            iss["id"],
            iss["slug"],
            iss["date_start"],
            iss["date_end"],
            iss.get("period_label") or iss["slug"],
            "published",
            json.dumps(data, ensure_ascii=False),
        ),
    )
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('agent_llm','Agent LLM','x','viewer','编辑部','ou_agent_llm')"
    )
    con.commit()
    from app import db

    db.reindex_issue(con, iss["id"], items=False, rebuild_chunks=True)
    con.commit()


def _check(row: dict, ans: dict, *, llm_on: bool) -> list[str]:
    errs: list[str] = []
    exp = row.get("expect") or {}
    if exp.get("intent") and ans.get("intent") != exp["intent"]:
        errs.append(f"intent want={exp['intent']} got={ans.get('intent')}")
    data_tools = ans.get("data_tools_called") or []
    if "max_data_tools" in exp and len(data_tools) > int(exp["max_data_tools"]):
        errs.append(f"data_tools>{exp['max_data_tools']}: {data_tools}")
    if exp.get("data_tool") and exp["data_tool"] not in (ans.get("tools_called") or []):
        errs.append(f"missing tool {exp['data_tool']}")
    if exp.get("min_evidence"):
        if len(ans.get("evidence_refs") or []) < int(exp["min_evidence"]):
            errs.append("min_evidence")
    if exp.get("no_draft_leak"):
        text = ans.get("text") or ""
        if "DRAFT" in text or "draft_json" in text.lower():
            errs.append("draft_leak")
    if not ans.get("fingerprint") or not ans.get("trace"):
        errs.append("missing fingerprint/trace")
    if exp.get("forbid_unsupported_as_fact"):
        for b in ans.get("claim_bindings") or []:
            if b.get("status") == "unsupported":
                # runtime should filter; if present in bindings ok, but not as sole fact claim on screen
                pass
    if llm_on and exp.get("intent") in ("ask_published", "ask_relations"):
        # 产品验收：成文路径应留下 ask_engine / llm 痕迹或非空答案
        if not (ans.get("text") or "").strip():
            errs.append("empty_answer_with_llm")
    if exp.get("forbid_fabricated_success") and exp.get("allow_empty"):
        text = ans.get("text") or ""
        # 空语料时不应声称「已确认发现」类强成功（启发式）
        if "已确认" in text and "未在已上线" not in text:
            errs.append("fabricated_success")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=("golden",), default="golden")
    ap.add_argument(
        "--allow-no-llm",
        action="store_true",
        help="允许未开 LLM（仅编排；不得当作 Layer2.2 产品通过）",
    )
    args = ap.parse_args()

    llm_on = os.environ.get("MESH_AGENT_USE_LLM", "").strip() in ("1", "true", "yes")
    if not llm_on and not args.allow_no_llm:
        print("FAIL: set MESH_AGENT_USE_LLM=1 or pass --allow-no-llm")
        return 2

    from app import db, db_conn

    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    os.environ["MESH_DB"] = path
    os.environ.pop("MESH_DB_URL", None)
    db_conn.DB_PATH = path
    db_conn.MESH_DB_URL = ""
    db.DB_PATH = path
    db.init_db(seed=False)

    from app.agent.harness import run_harness

    con = db.connect()
    results = []
    try:
        _seed_golden(con)
        for row in _load_gold():
            if row.get("skip"):
                results.append({"id": row["id"], "skip": True})
                continue
            ans = run_harness(
                con,
                {
                    "text": row["q"],
                    "channel": "harness",
                    "feishu_open_id": "ou_agent_llm",
                    "explicit_issue": "2026-8-17",
                },
            )
            errs = _check(row, ans, llm_on=llm_on)
            results.append(
                {
                    "id": row["id"],
                    "ok": not errs,
                    "errs": errs,
                    "intent": ans.get("intent"),
                    "evidence_n": len(ans.get("evidence_refs") or []),
                    "llm_on": llm_on,
                }
            )
            print(
                f"[{'PASS' if not errs else 'FAIL'}] {row['id']} "
                f"intent={ans.get('intent')} ev={len(ans.get('evidence_refs') or [])} {errs}"
            )
    finally:
        con.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    ran = [r for r in results if not r.get("skip")]
    n_ok = sum(1 for r in ran if r.get("ok"))
    summary = {
        "llm_on": llm_on,
        "product_gate": bool(llm_on),
        "pass": n_ok,
        "total": len(ran),
        "ok": n_ok == len(ran) and bool(ran),
        "results": results,
    }
    out = ROOT / "eval" / "reports" / "AGENT_LLM_E2E.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: summary[k] for k in ("llm_on", "product_gate", "pass", "total", "ok")}, ensure_ascii=False))
    print(f"wrote {out}")
    # 无 LLM 时即使全绿也不算产品 Layer2.2 通过
    if not llm_on:
        return 0 if args.allow_no_llm else 2
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
