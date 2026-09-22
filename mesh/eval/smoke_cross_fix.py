"""tmesh 只读冒烟：验证证据链回填 + 交叉集合运算在真实数据上的确定性结果。"""
from __future__ import annotations

import json
import os

os.environ["MESH_ASK_LLM_INTENT"] = "0"  # 只测确定性路径，不调 LLM

from app import ask_engine, db, qa_structured
from app.ask_planner import plan_retrieval
from app.ask_scope import AskScope

con = db.connect()
scope = AskScope(channel="web", role="viewer")

QS = [
    "一共有多少家公司在多个团队同时出现过？",
    "哪些公司在多个团队同时出现过？",
    "编辑部和商业化团队都接触过的公司有哪些？",
    "商业化团队跟进了但编辑部还没接触的公司有哪些？",
    "硅谷 BD 团队接触了、但编辑部还没接触的公司有哪些？",
    "投资团队和商业化团队共同关注的公司有哪些？",
]

print("=== planner ===")
for q in QS:
    p = plan_retrieval(q)
    print(f"  set_op={p.set_op:12s} conf={p.confidence:.2f}  {q}")

print("\n=== structured totals ===")
for q in QS:
    prep = ask_engine.prepare(con, q, scope)
    plan = prep.get("retrieval_plan") or {}
    print(f"  {q}\n    mode={prep.get('mode')} set_op={plan.get('set_op')} "
          f"total={prep.get('total')} n_hits={prep.get('n_hits')}")
    da = prep.get("direct_answer")
    if da:
        print(f"    direct_answer={da[:120]}")

print("\n=== multi_team details ===")
ctxs, total, meta = qa_structured.query_multi_team(con, None, None, kind="company")
print(f"  company entities in >=2 teams: {total} {meta}")
for c in ctxs[:8]:
    print("   -", c["标题"], "|", c["内容"][:110])

con.close()
