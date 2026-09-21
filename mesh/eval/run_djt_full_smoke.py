#!/usr/bin/env python3
"""Prod 全方面冒烟：以杜锦涛身份（feishu_open_id）跑多部门/多人员 query。

在容器内执行（复用生产 agent 路径 = 飞书 Bot 同 runtime，不刷真实群聊）：
  PYTHONPATH=/srv/mesh python /tmp/djt_full_smoke.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.agent.harness import run_harness  # noqa: E402

OUT = ROOT / "eval" / "reports" / "experiments" / "DJT_FULL_SMOKE.json"
OUT.parent.mkdir(parents=True, exist_ok=True)

# 多部门 × 多人员 × 问法类型（覆盖检索/跟进/团队/专名）
QUERIES: list[dict] = [
    # —— 本人 / 身份 ——
    {"id": "S01", "cat": "self", "q": "我是谁，我在哪个团队？"},
    {"id": "S02", "cat": "self", "q": "锦涛最近在忙什么？"},
    # —— 创新技术 ——
    {"id": "T01", "cat": "team_tech", "q": "创新技术团队这期周报重点是什么？"},
    # —— 编辑部 ——
    {"id": "E01", "cat": "dept_edit", "q": "编辑部这周接触了哪些公司和人？"},
    {"id": "E02", "cat": "person_edit", "q": "赵思琪最近在跟进什么？"},
    {"id": "E03", "cat": "person_edit", "q": "张山山这期写了什么？"},
    # —— 商业化 ——
    {"id": "B01", "cat": "dept_biz", "q": "商业化团队最近有哪些客户进展？"},
    {"id": "B02", "cat": "person_biz", "q": "郭冀超最近在忙什么？"},
    {"id": "B03", "cat": "person_biz", "q": "姜丽最近跟进了哪些项目？"},
    # —— 社区 ——
    {"id": "C01", "cat": "dept_community", "q": "社区运营最近在做什么活动？"},
    {"id": "C02", "cat": "person_community", "q": "胡晓最近在跟进什么？"},
    # —— 视频 / 内容 ——
    {"id": "V01", "cat": "dept_video", "q": "创意视频团队本期重点是什么？"},
    {"id": "V02", "cat": "person_video", "q": "闫晓龙最近拍了什么？"},
    # —— 专名 / 交叉 ——
    {"id": "X01", "cat": "entity", "q": "面壁智能最近有什么进展？"},
    {"id": "X02", "cat": "entity", "q": "英伟达 EDC 培训讲了什么？"},
    {"id": "X03", "cat": "entity", "q": "The Verge 写了哪些手机或隐私相关？"},
    {"id": "X04", "cat": "cross", "q": "编辑部和商业化最近有交叉的人和公司吗？"},
    # —— 时间 / 跟进 ——
    {"id": "W01", "cat": "time", "q": "最近一期周报里有什么值得关注的？"},
    {"id": "W02", "cat": "followup", "q": "他具体说了什么？"},  # 依赖上一条上下文
    {"id": "W03", "cat": "person_alias", "q": "思琪最近怎么样？"},
]


def _find_djt(con) -> dict:
    rows = con.execute(
        "SELECT id, username, display, role, team, feishu_open_id FROM users "
        "WHERE display LIKE ? OR username LIKE ? OR display LIKE ? "
        "ORDER BY id DESC LIMIT 10",
        ("%杜锦涛%", "%杜锦涛%", "%锦涛%"),
    ).fetchall()
    cands = [dict(r) for r in (rows or [])]
    for r in cands:
        if "杜锦涛" in str(r.get("display") or ""):
            return r
    return cands[0] if cands else {}


def _answer_text(d: dict) -> str:
    return str(
        d.get("display_text")
        or d.get("text")
        or d.get("answer")
        or ""
    ).strip()


def _ok(d: dict, text: str) -> tuple[bool, str]:
    if d.get("error"):
        return False, f"error={d.get('error')}"
    if not text:
        return False, "empty_answer"
    bad = ("unsupported_or_empty", "未在已上线周报", "出错了", "Traceback")
    # 「未在已上线周报」对部分问法可接受，但计 soft
    if any(b in text for b in ("出错了", "Traceback", "Internal Server Error")):
        return False, "hard_fail_in_text"
    if len(text) < 8:
        return False, "too_short"
    return True, "ok"


def main() -> int:
    con = db.connect()
    try:
        user = _find_djt(con)
        if not user or not user.get("feishu_open_id"):
            print("FAIL: 找不到杜锦涛或其 feishu_open_id", user)
            return 2
        open_id = str(user["feishu_open_id"])
        print(
            f"identity display={user.get('display')} id={user.get('id')} "
            f"team={user.get('team')} open_id={open_id[:12]}…",
            flush=True,
        )

        session_id = f"djt-smoke-{int(time.time())}"
        results = []
        t0 = time.time()
        for i, row in enumerate(QUERIES, 1):
            qid, q = row["id"], row["q"]
            print(f"[{i}/{len(QUERIES)}] {qid} {q[:36]}…", flush=True)
            payload = {
                "text": q,
                "channel": "harness",
                "feishu_open_id": open_id,
                "mesh_user_id": int(user["id"]),
                "session_id": session_id,
                "request_id": f"djt-{qid}",
            }
            t1 = time.time()
            try:
                ans = run_harness(con, payload)
                err = None
            except Exception as e:
                ans = {}
                err = f"{type(e).__name__}: {e}"
                traceback.print_exc()
            elapsed = int((time.time() - t1) * 1000)
            text = _answer_text(ans) if ans else ""
            ok, reason = (False, err or "exception") if err else _ok(ans, text)
            soft = ("未在已上线周报" in text) or ("没捞到" in text) or ("没有找到" in text)
            results.append(
                {
                    "id": qid,
                    "cat": row["cat"],
                    "query": q,
                    "ok": ok,
                    "soft_empty": soft,
                    "reason": reason,
                    "elapsed_ms": elapsed,
                    "answer_preview": text[:280],
                    "answer_len": len(text),
                    "tools": (ans.get("observability") or {}).get("tools")
                    or ans.get("tool_trace")
                    or [],
                    "mode": (ans.get("observability") or {}).get("mode")
                    or ans.get("mode"),
                }
            )
            flag = "OK" if ok else "FAIL"
            if soft:
                flag = "SOFT"
            print(f"  → {flag} {elapsed}ms len={len(text)} {reason}", flush=True)

        by_cat = defaultdict(list)
        for r in results:
            by_cat[r["cat"]].append(r)

        summary = {
            "identity": {
                "display": user.get("display"),
                "user_id": user.get("id"),
                "team": user.get("team"),
                "feishu_open_id_prefix": open_id[:16],
            },
            "n": len(results),
            "n_ok": sum(1 for r in results if r["ok"]),
            "n_soft_empty": sum(1 for r in results if r.get("soft_empty")),
            "n_fail": sum(1 for r in results if not r["ok"]),
            "elapsed_s": round(time.time() - t0, 1),
            "by_cat": {
                k: {
                    "n": len(v),
                    "ok": sum(1 for x in v if x["ok"]),
                    "soft": sum(1 for x in v if x.get("soft_empty")),
                }
                for k, v in by_cat.items()
            },
            "results": results,
            "pass": sum(1 for r in results if not r["ok"]) == 0,
        }
        OUT.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({k: summary[k] for k in summary if k != "results"}, ensure_ascii=False, indent=2))
        print("wrote", OUT)
        return 0 if summary["pass"] else 1
    finally:
        con.close()


if __name__ == "__main__":
    raise SystemExit(main())
