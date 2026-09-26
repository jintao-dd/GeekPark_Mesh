import json
from app import db
from app.agent.harness import run_harness

questions = [
    "编辑部接触了面壁智能吗",
    "具身智能有哪些公司",
    "詹杨帆最近有什么动态",
    "视频号团队最近关注了什么",
    "商业化团队在跟进的客户里，哪些同时也是编辑部的采访对象？",
    "各团队最近关注了哪些硬件相关话题？",
    "硅谷 BD 团队有没有接触面壁智能",
    "近30天编辑部接触了谁",
    "除了品牌创意之外，其它任何团队的信息或讨论记录中出现了与“设计”相关的信息？",
    "是否有同事提及了新的公司季度或年度项目？其中可能与品牌创意相关的信息有哪些？",
    "除了商业化团队之外，其它任何团队的信息或讨论记录中，特别是编辑部的选题讨论之中，有任何与商业化团队的讨论中有重合的公司或人？具体讨论了什么？",
    "除了总裁办之外，其它任何团队的信息或讨论记录中，列出所有同一个公司或人但是有公司内多个团队在接触的情况。",
    "列出并总结所有已经确定正在进行的跨部门协作项目。",
    "除了硅谷BD团队之外，其它任何团队的信息或讨论记录中，列出所有商业化团队计划在海外参与或策划的活动。",
    "列出所有编辑部和founder park团队新接触团队或人，并分别用一句话介绍这个团队或人。",
    "列出其它团队所有与硅谷BD正在同时接触或者可能潜在同时接触的公司或人的信息。",
]

con = db.connect()
try:
    for q in questions:
        print(f"=== Q: {q} ===")
        out = run_harness(con, {
            "text": q,
            "feishu_open_id": "ou_fd65363b8ed1e5ddb93dd56e86a35b9b",
            "channel": "harness",
        })
        print("intent:", out.get("intent"))
        print("text:", out.get("text", "")[:800])
        print("display_text:", out.get("display_text", "")[:800])
        print("tools_called:", out.get("tools_called"))
        print("refused:", out.get("refused"))
        print("deny_reason:", out.get("deny_reason"))
        print()
finally:
    con.close()
