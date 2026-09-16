#!/usr/bin/env python3
"""Layer 2.2：Agent 真实 LLM E2E（10 题 Gold）。

要求 MESH_AGENT_USE_LLM=1；验证 LLM 成文 + Evidence + fingerprint/trace。

用法：
  MESH_AGENT_USE_LLM=1 python eval/run_agent_llm_e2e.py --corpus golden
  MESH_AGENT_USE_LLM=1 python eval/run_agent_llm_e2e.py --reuse-env-db   # tmesh PG

不并入 local_regression 万能脚本。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

GOLD = ROOT / "eval" / "agent_e2e_gold_v1.jsonl"
ISSUE_EXPORT = ROOT / "deploy" / "_issue_2026-8-17.json"
OPEN_ID = "ou_agent_llm"
ISSUE = "2026-8-17"


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
        "VALUES ('agent_llm','Agent LLM','x','viewer','编辑部',?)",
        (OPEN_ID,),
    )
    con.commit()
    from app import db

    db.reindex_issue(con, iss["id"], items=False, rebuild_chunks=True)
    con.commit()


def _ensure_tmesh_user(con) -> None:
    row = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (OPEN_ID,)
    ).fetchone()
    if row:
        return
    con.execute(
        "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
        "VALUES ('agent_llm','Agent LLM','x','viewer','编辑部',?)",
        (OPEN_ID,),
    )
    con.commit()


def _check(row: dict, ans: dict, *, llm_on: bool) -> list[str]:
    errs: list[str] = []
    exp = row.get("expect") or {}
    text = ans.get("text") or ""

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
        if "DRAFT" in text or "draft_json" in text.lower() or "SHOULD_NOT_SEE" in text:
            errs.append("draft_leak")

    if not ans.get("fingerprint") or not isinstance(ans.get("trace"), dict):
        errs.append("missing fingerprint/trace")

    # unsupported 不得作为用户可见事实句（runtime 应过滤 claim_bindings）
    if exp.get("forbid_unsupported_as_fact"):
        for b in ans.get("claim_bindings") or []:
            if b.get("status") == "unsupported":
                errs.append("unsupported_binding_visible")
                break

    if llm_on and exp.get("require_llm") and exp.get("intent") == "ask_published":
        if not (ans.get("trace") or {}).get("llm_used"):
            errs.append("llm_not_used")

    if llm_on and exp.get("intent") in ("ask_published", "ask_relations"):
        if not text.strip():
            errs.append("empty_answer_with_llm")

    if exp.get("forbid_fabricated_success") and exp.get("allow_empty"):
        if "已确认" in text and "未在已上线" not in text:
            errs.append("fabricated_success")
        if "AGENT_E2E_EMPTY_XYZ" in text:
            positive = re.search(
                r"(找到|发现|检索到).{0,12}AGENT_E2E_EMPTY_XYZ", text
            )
            negative = re.search(
                r"(没有|未|无|无法).{0,24}AGENT_E2E_EMPTY_XYZ", text
            )
            if positive and not negative:
                errs.append("fabricated_marker")

    return errs


def _payload_for(row: dict) -> dict:
    if row.get("id") == "a06" or row.get("identity") == "unlinked":
        return {
            "text": row["q"] if row.get("q") and not row.get("skip") else "本周编辑部关注什么",
            "channel": "harness",
            "feishu_open_id": "ou_never_bound_llm_e2e",
            "explicit_issue": ISSUE,
        }
    return {
        "text": row["q"],
        "channel": "harness",
        "feishu_open_id": OPEN_ID,
        "explicit_issue": ISSUE,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", choices=("golden",), default="golden")
    ap.add_argument(
        "--reuse-env-db",
        action="store_true",
        help="使用当前 MESH_DB_URL（tmesh）；需已有 published 2026-8-17",
    )
    ap.add_argument("--allow-no-llm", action="store_true")
    args = ap.parse_args()

    os.environ["MESH_AGENT_USE_LLM"] = os.environ.get("MESH_AGENT_USE_LLM", "").strip() or (
        "" if args.allow_no_llm else os.environ.get("MESH_AGENT_USE_LLM", "")
    )
    llm_on = os.environ.get("MESH_AGENT_USE_LLM", "").strip() in ("1", "true", "yes")
    if not llm_on and not args.allow_no_llm:
        print("FAIL: set MESH_AGENT_USE_LLM=1 or pass --allow-no-llm")
        return 2

    from app import db, db_conn
    from app.agent.harness import run_harness

    tmp_path = None
    if args.reuse_env_db:
        con = db.connect()
        _ensure_tmesh_user(con)
        pub = con.execute(
            "SELECT status FROM issues WHERE slug=?", (ISSUE,)
        ).fetchone()
        if not pub or pub["status"] != "published":
            print(f"FAIL: issue {ISSUE} not published on env db")
            con.close()
            return 2
    else:
        fd, tmp_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        os.environ["MESH_DB"] = tmp_path
        os.environ.pop("MESH_DB_URL", None)
        db_conn.DB_PATH = tmp_path
        db_conn.MESH_DB_URL = ""
        db.DB_PATH = tmp_path
        db.init_db(seed=False)
        con = db.connect()
        _seed_golden(con)

    results = []
    try:
        for row in _load_gold():
            # a06：未绑定探测（覆盖 skip）
            if row.get("id") == "a06":
                row = {
                    **row,
                    "skip": False,
                    "q": "本周编辑部关注什么",
                    "expect": {
                        "intent": "refuse",
                        "max_data_tools": 0,
                    },
                }
            if row.get("skip"):
                results.append({"id": row["id"], "skip": True})
                continue

            ans = run_harness(con, _payload_for(row))
            errs = _check(row, ans, llm_on=llm_on)
            rec = {
                "id": row["id"],
                "ok": not errs,
                "errs": errs,
                "intent": ans.get("intent"),
                "evidence_n": len(ans.get("evidence_refs") or []),
                "llm_used": (ans.get("trace") or {}).get("llm_used"),
                "fingerprint": ans.get("fingerprint"),
                "text_head": (ans.get("text") or "")[:220],
                "llm_on": llm_on,
            }
            results.append(rec)
            print(
                f"[{'PASS' if not errs else 'FAIL'}] {row['id']} "
                f"intent={ans.get('intent')} ev={rec['evidence_n']} "
                f"llm_used={rec['llm_used']} {errs}"
            )
            print(f"    text: {rec['text_head'][:120]}")
    finally:
        con.close()
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    ran = [r for r in results if not r.get("skip")]
    n_ok = sum(1 for r in ran if r.get("ok"))
    summary = {
        "llm_on": llm_on,
        "product_gate": bool(llm_on),
        "reuse_env_db": bool(args.reuse_env_db),
        "issue": ISSUE,
        "pass": n_ok,
        "total": len(ran),
        "ok": n_ok == len(ran) and bool(ran),
        "results": results,
    }
    out = ROOT / "eval" / "reports" / "AGENT_LLM_E2E.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {k: summary[k] for k in ("llm_on", "product_gate", "pass", "total", "ok", "reuse_env_db")},
            ensure_ascii=False,
        )
    )
    print(f"wrote {out}")
    if not llm_on:
        return 0 if args.allow_no_llm else 2
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
