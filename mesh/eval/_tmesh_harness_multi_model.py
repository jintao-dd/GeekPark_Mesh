"""多模型对比测试：用同一批问题在不同 answer 模型下跑 harness。

环境变量控制：
- MESH_LLM_MODEL_ANSWER：被测模型（脚本会覆盖）
- MESH_HARNESS_OPEN_ID：测试用户 feishu_open_id
"""
import json
import os
import time

from app import db, llm
from app.agent.harness import run_harness

QUESTIONS = [
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

MODELS = [
    "anthropic/claude-4.8-opus",
    "anthropic/claude-4.7-opus",
    "anthropic/claude-opus-5",
    "deepseek/deepseek-v4-pro",
    "deepseek/deepseek-v3.1-terminus",
    "openai/gpt-5.4-pro",
    "openai/gpt-5-pro",
    "alibaba/qwen3-235b-a22b",
    "zhipu/glm-5.1",
    "minimax/minimax-m3",
]

OPEN_ID = os.environ.get("MESH_HARNESS_OPEN_ID") or "ou_fd65363b8ed1e5ddb93dd56e86a35b9b"


def _run_one(con, q: str, model: str) -> dict:
    """单问题单模型跑一轮，返回关键字段。"""
    os.environ["MESH_LLM_MODEL_ANSWER"] = model
    # 刷新 llm 模块缓存的 task→model 映射
    llm._MODEL_CACHE.clear()  # noqa: SLF001
    t0 = time.monotonic()
    out = run_harness(con, {
        "text": q,
        "feishu_open_id": OPEN_ID,
        "channel": "harness",
    })
    latency_ms = int((time.monotonic() - t0) * 1000)
    return {
        "model": model,
        "intent": out.get("intent"),
        "text": out.get("text", ""),
        "display_text": out.get("display_text", ""),
        "tools_called": out.get("tools_called"),
        "refused": out.get("refused"),
        "deny_reason": out.get("deny_reason"),
        "latency_ms": latency_ms,
    }


def main() -> None:
    results: list[dict] = []
    con = db.connect()
    try:
        for model in MODELS:
            print(f"\n# MODEL: {model}\n")
            for q in QUESTIONS:
                print(f"=== Q: {q} ===")
                try:
                    r = _run_one(con, q, model)
                except Exception as e:
                    r = {
                        "model": model,
                        "question": q,
                        "error": str(e),
                    }
                r["question"] = q
                results.append(r)
                print(json.dumps({
                    "model": r.get("model"),
                    "intent": r.get("intent"),
                    "refused": r.get("refused"),
                    "latency_ms": r.get("latency_ms"),
                    "text_preview": (r.get("text") or "")[:300],
                }, ensure_ascii=False))
    finally:
        con.close()

    out_path = "/tmp/_tmesh_harness_multi_model.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved results to {out_path}")


if __name__ == "__main__":
    main()
