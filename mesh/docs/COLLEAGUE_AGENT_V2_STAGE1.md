# Colleague Agent v2 · Stage 1 — Colleague Controller

> Gate 文档。Grounded Brain（Retrieval / Ranking / Claim / Evidence / Gold）冻结。

## 目标

统一决策中枢：判断「这句话该怎么处理」，而不是无限加 `conversation.py` 规则。

Controller **只决策、不回答、不当事实源**。

## 决策结构

```json
{
  "mode": "conversation|content|enterprise|followup|clarify|meta|system",
  "intent": "...",
  "entities": [],
  "topic": "",
  "needs_grounding": false,
  "needs_clarification": false,
  "response_mode": "direct|conversational|clarify|...",
  "context_refs": []
}
```

## 预算

| 情况 | Router LLM |
|------|------------|
| 明显 chat / meta / enterprise / follow-up / clarify | **0**（fast-path） |
| 低置信 / 难分边界 | **最多 1×** `task=controller` |

模型：`MESH_LLM_MODEL_CONTROLLER` → 回落 `MESH_LLM_MODEL_SEMANTIC` → `MESH_LLM_MODEL`。

关闭 LLM：`MESH_COLLEAGUE_CONTROLLER_LLM=0`（低置信默认 clarify，避免瞎查）。

## 代码

- `app/agent/colleague_controller.py` — 中枢
- `app/agent/conversation.py` — fast-path 规则（保留）
- `app/agent/intent.py` / `runtime.py` — 经 Controller 统一出口；trace 带 `controller` + `router_llm_used`

## Stage 1 不做

Persona / 情绪 / 主动性 / L2 Memory / 多实例 Session / Tool / ReAct / Multi-Agent / Wiki·ES·Graph / Ranking·Claim·Retrieval

## Gate

场景：`eval/colleague_controller_stage1.jsonl`（含边界人话）

```bash
python -m eval.run_colleague_controller_stage1
```

指标：

- `wrong_route`
- `retrieval_when_unneeded` → 必须 0
- `missed_grounding` → 必须 0
- `clarification_accuracy`
- `router_budget_violations` → 必须 0

报告：`eval/reports/COLLEAGUE_CONTROLLER_STAGE1.{json,md}`

质量不回退（Brain 未改）：Canonical / Unseen / S01–S05 保持既有基线；本 Stage 不重跑全量 Ask Gold，除非 Controller 误伤 enterprise 路由。

## Next

Stage 1 Gate PASS → Stage 2 Conversation / Persona（用人味，不再改「何时查」）。
