"""Q16 单问题多模型对比：验证截断修复和模型差异。"""
import json
import os
import time

from app import db, llm
from app.agent.harness import run_harness

Q = "列出其它团队所有与硅谷BD正在同时接触或者可能潜在同时接触的公司或人的信息。"

MODELS = [
    "anthropic/claude-4.8-opus",
    "anthropic/claude-opus-5",
    "deepseek/deepseek-v4-pro",
    "openai/gpt-5.4-pro",
    "alibaba/qwen3-235b-a22b",
]

OPEN_ID = os.environ.get("MESH_HARNESS_OPEN_ID") or "ou_fd65363b8ed1e5ddb93dd56e86a35b9b"


def main() -> None:
    con = db.connect()
    results = []
    try:
        for model in MODELS:
            print(f"\n# MODEL: {model}")
            os.environ["MESH_LLM_MODEL_ANSWER"] = model
            llm._MODEL_CACHE.clear()  # noqa: SLF001
            t0 = time.monotonic()
            out = run_harness(con, {
                "text": Q,
                "feishu_open_id": OPEN_ID,
                "channel": "harness",
            })
            latency_ms = int((time.monotonic() - t0) * 1000)
            r = {
                "model": model,
                "intent": out.get("intent"),
                "text": out.get("text", ""),
                "display_text": out.get("display_text", ""),
                "tools_called": out.get("tools_called"),
                "refused": out.get("refused"),
                "latency_ms": latency_ms,
            }
            results.append(r)
            print(f"  intent={r['intent']} refused={r['refused']} latency={latency_ms}ms")
            print(f"  text_len={len(r['text'])} display_len={len(r['display_text'])}")
            print(f"  preview: {r['text'][:200]}...")
    finally:
        con.close()

    out_path = "/tmp/_tmesh_harness_q16_models.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\nSaved results to {out_path}")


if __name__ == "__main__":
    main()
