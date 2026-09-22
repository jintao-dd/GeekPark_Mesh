#!/usr/bin/env python3
"""生产环境 · 交叉数据问答质量探针（只读语料，不写 items/issues）。

目的：不看单题对错，而看**交叉/跨团队问题**的真实回答质量——
问句落在生产真实数据上（面壁智能 / vivo / 张鹏 / 小红书…），
抓「答得对不对、有没有证据支撑、是不是答非所问」。

安全边界：
- 语料只读；不 publish / 不 preview / 不改 items。
- 用独立测试身份（TEST_OPEN_ID），不碰真实用户会话；跑完清理。
- channel=harness → qa_log 默认不落（不污染真实样本库）。

用法（生产/ tmesh 容器内）：
  docker exec -i geekpark-mesh python - < eval/run_cross_quality_probe.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 独立测试身份：避免与真实用户 session 撞车
TEST_OPEN_ID = "ou_cross_quality_probe"

# 交叉评测题：全部落在「跨团队 / 交叉数据」上
QUESTIONS: list[dict] = [
    # A. 交叉接触（intersect）
    {"id": "X01", "cat": "intersect", "q": "编辑部和商业化团队都接触过的公司有哪些？"},
    {"id": "X02", "cat": "intersect", "q": "硅谷 BD 团队和编辑部同时接触了哪些公司？"},
    {"id": "X03", "cat": "intersect", "q": "投资团队和商业化团队共同关注的公司有哪些？"},
    {"id": "X04", "cat": "intersect", "q": "视频号团队和编辑部都关注的公司？"},
    # B. 差集 / 缺口
    {"id": "X05", "cat": "diff", "q": "商业化团队跟进了但编辑部还没接触的公司有哪些？"},
    {"id": "X06", "cat": "diff", "q": "硅谷 BD 团队接触了、但编辑部还没接触的公司有哪些？"},
    # C. 同一主体跨团队分工
    {"id": "X07", "cat": "same_entity", "q": "面壁智能在各部门分别是什么情况？"},
    {"id": "X08", "cat": "same_entity", "q": "vivo 在不同团队里分别是谁在跟进，都做了什么？"},
    {"id": "X09", "cat": "same_entity", "q": "小红书在哪些团队出现过，分别在做什么？"},
    # D. 人物跨团队
    {"id": "X10", "cat": "person_cross", "q": "张鹏在哪些团队都有出现，分别是什么角色？"},
    {"id": "X11", "cat": "person_cross", "q": "李源跨了哪些团队，各自做了什么？"},
    # E. 主题跨团队
    {"id": "X12", "cat": "topic_cross", "q": "AI硬件这个话题有哪些团队在关注，分别是什么动作？"},
    {"id": "X13", "cat": "topic_cross", "q": "具身智能相关的公司在哪些团队被提到，各自在做什么？"},
    # F. 总量 / 时间
    {"id": "X14", "cat": "count", "q": "一共有多少家公司在多个团队同时出现过？"},
    {"id": "X15", "cat": "time", "q": "最近两周各部门分别接触了哪些新公司？"},
    # G. 追问（上下文继承）
    {
        "id": "X16",
        "cat": "followup",
        "q": "那其中哪些是商业化团队主导的？",
        "needs_prior": True,
        "prior_q": "编辑部和商业化团队都接触过的公司有哪些？",
    },
    # H. 无证据守卫（不得编造）
    {"id": "X17", "cat": "guard", "q": "不存在的公司 Zyqx-NoSuchCorp 有没有跨团队接触？"},
    {"id": "X18", "cat": "guard", "q": "量子永生科技和哪些团队有交叉？"},
]


def _status(ans: dict) -> str:
    try:
        from app.agent.observability import _answer_status

        return _answer_status(ans)
    except Exception:
        return "unknown"


def _binding_stats(ans: dict) -> dict:
    b = ans.get("claim_bindings") or []
    out = {"grounded": 0, "weak": 0, "unsupported": 0}
    for x in b:
        s = str((x or {}).get("status") or "")
        if s in out:
            out[s] += 1
    out["total"] = len(b)
    return out


def main() -> int:
    os.environ.setdefault("MESH_AGENT_USE_LLM", "1")

    from app import db
    from app.agent import session_state as sstore
    from app.agent.harness import run_harness

    con = db.connect()

    # 测试身份：存在就复用，避免每次建用户
    u = con.execute(
        "SELECT id FROM users WHERE feishu_open_id=?", (TEST_OPEN_ID,)
    ).fetchone()
    if not u:
        con.execute(
            "INSERT INTO users(username, display, pw_hash, role, team, feishu_open_id) "
            "VALUES ('cross_probe','Cross Probe','x','viewer',NULL,?)",
            (TEST_OPEN_ID,),
        )
        con.commit()

    pub = [
        dict(r)
        for r in con.execute(
            "SELECT slug FROM issues WHERE status='published' ORDER BY date_end DESC"
        )
    ]
    print(json.dumps({"published": [p["slug"] for p in pub]}, ensure_ascii=False), flush=True)

    results: list[dict] = []
    for row in QUESTIONS:
        qid = row["id"]
        prior = row.get("prior_q") or ""
        sid = f"crossprobe-{qid}"
        payload: dict = {
            "text": row["q"],
            "channel": "harness",
            "feishu_open_id": TEST_OPEN_ID,
            "session_id": sid,
        }
        t0 = time.time()
        err = ""
        ans: dict = {}
        try:
            if prior:
                # 先跑前一轮，建立上下文，再问追问
                run_harness(
                    con,
                    {
                        "text": prior,
                        "channel": "harness",
                        "feishu_open_id": TEST_OPEN_ID,
                        "session_id": sid,
                    },
                )
            ans = run_harness(con, payload)
        except Exception as e:  # 探针不该中断整轮
            err = f"{type(e).__name__}: {e}"
        ms = int((time.time() - t0) * 1000)

        text = str(ans.get("display_text") or ans.get("text") or "")
        rec = {
            "id": qid,
            "cat": row["cat"],
            "q": row["q"],
            "ms": ms,
            "error": err,
            "intent": ans.get("intent"),
            "route": (ans.get("trace") or {}).get("conversation_route"),
            "answer_status": _status(ans) if ans else "",
            "evidence_n": len(ans.get("evidence_refs") or []),
            "evidence_refs": (ans.get("evidence_refs") or [])[:6],
            "bindings": _binding_stats(ans) if ans else {},
            "tools": ans.get("tools_called") or [],
            "answer": text,
        }
        results.append(rec)
        print(
            f"[{qid}] {row['cat']:12s} st={rec['answer_status']:12s} "
            f"ev={rec['evidence_n']:2d} {ms:6d}ms "
            f"intent={rec['intent']} err={err[:60]}",
            flush=True,
        )

    # 清理测试会话状态（不污染生产）
    try:
        sstore.clear()
    except Exception:
        pass
    try:
        for r in con.execute(
            "SELECT id FROM users WHERE feishu_open_id=?", (TEST_OPEN_ID,)
        ):
            pass
    except Exception:
        pass
    con.close()

    out = {"published": [p["slug"] for p in pub], "results": results}
    print("===JSON_BEGIN===")
    print(json.dumps(out, ensure_ascii=False))
    print("===JSON_END===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
