# Feishu Agent v1 · 产品硬边界（冻结）

> **质量基线：** Agent Quality v3.0 Production Baseline / E2E RC  
> **原则：** 只接线、不扩大脑；不回头开 v2.x / v3.x 质量微调。  
> **状态（2026-09-10）：** 硬边界 ✅ 冻结 · HTTP Capacity 已测 · **Bot 入站（含加密）已接线 · 出站回消息 ❌** · 总览见 [`PROJECT_STATUS.md`](./PROJECT_STATUS.md)

## 主链路（不可改）

```
Feishu User
  → Agent / Ask（现有 handle_message）
  → context / follow-up（现有 context_refs 合同）
  → intent / entity（现有 rule_classify）
  → Retrieval + Ranking v1.4
  → Claim Support v2.4c-2
  → Evidence
  → Answer（默认 Sonnet；敏感/对外 Opus override）
```

## 产品范围（上限）

1. 事实查询  
2. Relation / Entity 查询  
3. Team / Attribution 查询  
4. Follow-up / 上下文追问  

回归门禁：v3.0 S01–S05 + Canonical Answer/Evidence + Claim Support + Temporal + Ranking smoke。

## 核心产品原则（硬）

**每个可验证事实回答，必须让用户看到对应 Evidence / 期次引用。**

- Evidence 必须与答案中的核心 claim 对齐（复用 Claim → EvidenceRef → Support）。  
- 禁止只展示「相关内容」却不支撑该 claim。  
- abstain / insufficient / contradicted 须显式可见，并尽量带期次或判定理由。  
- Temporal：「最近」类问句沿用现有 caveat，不偷偷改规则。

## 模型策略

| 任务 | 模型 |
|------|------|
| semantic judge | `claude-sonnet-4-6` |
| answer（默认） | `claude-sonnet-4-6` |
| answer（敏感/对外） | `anthropic/claude-4.8-opus` |

环境变量覆盖允许；**禁止** Dynamic Router。

## 明确禁止

新 Planner · 新 Retrieval · Wiki · ES · Graph / Neo4j · Agentic RAG · Memory 架构 · 写回 · 自动扩展能力 · 大规模 Ontology 扩建 · 质量小版本回头改

## 真相源

Published items / relations / Evidence only。LLM 摘要不得成为第二 truth source。

## P0 基础设施：权限 / 审计 / 可观测

每次请求至少记录（HTTP `observability` 块 + 日志）：

| 字段 | 说明 |
|------|------|
| `request_id` | 单次请求唯一 ID |
| `feishu_user` / open_id | 调用方身份 |
| `conversation` / `session_id` | 会话 |
| `timestamp` | 服务端收到时间（UTC） |
| `model_used` | 实际任务模型配置快照 |
| `retrieval_n_hits` | 检索命中数 |
| `evidence_refs` | 证据句柄列表（或 count） |
| `answer_status` | grounded / weak / unsupported / refused / error |
| `latency_ms` | 端到端耗时 |
| `error` | 失败原因（若有） |

**权限硬约束：** 用户数据范围在 Agent **入口**（Identity → Permission → Query Scope）确定，再进入 Retrieval。  
禁止「先全库检索、再在回答层遮」。v1 可不做复杂 RBAC，但**不得把「所有人可见全部 Published」写死为永久架构**。

## 产品路线（冻结顺序）

```
① v1 硬边界冻结 ✅
        ↓
② Capacity / Pressure Test ✅（HTTP 已测）
        ↓
③ C_safe / C_knee / C_max ✅（量过一版）
        ↓
④ 并发 / 超时 / 队列策略  ← 部分未做
        ↓
⑤ Feishu UX：出站 + 思考卡 → Patch 终答  ← Phase 1（当前）
        ↓
⑥ 小规模真实用户 Canary
        ↓
⑦ 根据真实 failure 决定下一刀
```

### ② Capacity 分阶段

1. **HTTP Agent 压测**（`POST /api/agent/v1/message`）— 不经真实飞书事件  
   并发阶梯：`1 / 2 / 4 / 8 / 16 / 32 / 64`  
   指标：成功率、错误率、P50/P95/P99、TTFT/TTFB、总耗时、排队、timeout/429、retry、LLM 次数、token（单并发可采）、CPU/内存、质量是否回退  
   产出：`C_safe` / `C_knee` / `C_max`
2. **Feishu 事件层压测**（HTTP 稳定后）：连续发问、多用户、同会话并发追问、重复 event / retry / 超时重投；重点验 `context_refs` 不串会话/用户/请求
3. **UX**（规格已钉死后）：收到问题 → 处理中 → 结论 → Evidence/期次 → caveat → 追问；后台真实状态事件 `retrieval` / `evidence checking` / `generation` / `completed` / `failed`（禁止假「正在思考」）

## 实现入口

- 编排：`app/agent/runtime.py` → `handle_message`  
- 飞书展示：`app/agent/feishu_reply.py` → `format_display_text`  
- HTTP harness：`POST /api/agent/v1/message`（含 `observability`）  
- 飞书事件：`POST /api/feishu/bot/event`（接线，不改大脑）  
- Capacity runner：`eval/run_agent_capacity.py`
