# Agent Capacity / E2E Pressure Test · Phase 1（HTTP Agent）

**target** tmesh `POST /api/agent/v1/message` · **issue** `2026-8-17`  
**不经**真实飞书事件层 · **DB pool** `min=2 max=20`

## Verdict（给策略用）

| 口径 | C_safe | C_knee | C_max | 说明 |
|------|--------|--------|-------|------|
| **A. 无 LLM（检索+摘要）** | **1** | **2** | **4** | P95 从 ~1.2s → C=2 已 ~22s；C=4 开始 timeout；C=64 大量 401/失败 |
| **B. 有 LLM（`MESH_AGENT_USE_LLM=1`）** | **≤2（产品）** / 阶梯内相对稳定到 8 | 未在 1..8 触达 | 未在 1..8 触达 | C=1..8 成功率 100%，但 **P95 ≈ 107–138s**；相对基线未恶化，不代表体验可接受 |

**企业服务建议（先定上限，再谈 UX）：**

1. **对外并发硬上限先按 `C_safe≈1～2` 排队**（尤其无队列时）；不要按 LLM 阶梯「8 仍 100%」放开。  
2. **单请求超时**：HTTP/飞书侧建议 **60–90s** 可失败+重试提示；当前 LLM P95>100s，需队列+状态事件，不能同步干等。  
3. **池子**：`MESH_DB_POOL_MAX=20` 已是无 LLM 高并发时的主要墙；C=64 出现 `401 未登录` 与 timeout，符合连接池耗尽/鉴权查库失败表象。  
4. **模型**：tmesh 默认 `MESH_LLM_MODEL=anthropic/claude-4.8-opus`（重）；Capacity 未改模型策略。Feishu 默认仍应按冻结表 Sonnet，敏感才 Opus。  
5. **质量抽检**：无 LLM 档在成功请求上 evidence_ux≈100%；失败档随错误率掉。LLM 档 1..8 全成功且带 Evidence 展示字段。

原始数据：

- `eval/reports/AGENT_CAPACITY_PRESSURE.tmesh.no_llm.{json,md}`
- `eval/reports/AGENT_CAPACITY_PRESSURE.tmesh.llm.{json,md}`

## A. 无 LLM 阶梯（1→64）

| C | n | success | timeout | 429 | P50 | P95 | P99 | rps | mem after |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 100% | 0 | 0 | 1164 | 1164 | 1164 | 0.86 | 111MiB |
| 2 | 2 | 100% | 0 | 0 | 13119 | 22602 | 23444 | 0.09 | 124MiB |
| 4 | 4 | 75% | 1 | 0 | 6088 | 103374 | 116750 | 0.03 | 137MiB |
| 8 | 8 | 100% | 0 | 0 | 4842 | 75069 | 103494 | 0.07 | 187MiB |
| 16 | 16 | 94% | 1 | 0 | 5958 | 50222 | 106065 | 0.13 | 286MiB |
| 32 | 32 | 94% | 2 | 0 | 22924 | 110677 | 120087 | 0.27 | 368MiB |
| 64 | 64 | 61% | 1 | 0 | 2983 | 51059 | 116274 | 0.53 | 476MiB |

- C=64 错误样本含大量 `HTTP 401 未登录`（高并发下鉴权/DB 路径失稳）。  
- **无 429**（应用层未做限流；失败表现为 timeout/401/变慢）。  
- CPU 采样始终很低（~0.2–0.3%），瓶颈不在 CPU。

启发式：`C_safe=1`（P95≤2×基线且 success≥99%）；`C_knee=2`；`C_max=4`（success<90% 或 timeout 抬头）。  
注：C=8 曾回升到 100%（小样本噪声）；**仍以首次恶化点作为 knee/max 决策输入**。

## B. 有 LLM 阶梯（1→8）

| C | n | success | timeout | 429 | P50 | P95 | P99 | rps |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 100% | 0 | 0 | ~138s | **137.6s** | ~138s | 0.007 |
| 2 | 2 | 100% | 0 | 0 | — | **114.1s** | — | 0.017 |
| 4 | 4 | 100% | 0 | 0 | — | **121.9s** | — | 0.033 |
| 8 | 8 | 100% | 0 | 0 | — | **107.1s** | — | 0.066 |

- 相对延迟启发式会把 `C_safe` 标到 8（基线已是 138s，2× 很难触发）——**产品上应否决**，改用绝对 SLA（例如 P95≤20s / ≤45s）重标定。  
- 响应里 `llm_used`（答案成文）计数为 0；墙钟仍到 100s+，更可能来自 **Claim semantic / 其它 LLM 调用** 或重模型链路，而非纯 FTS。定模型策略时要分开看 answer vs semantic。  
- token 进程累加在并发下不可靠，本报告不以 token 定论。

## 观测性

本次一并落地（不改大脑）：

- 响应 `request_id` + `observability`（user / session / model_used / evidence_count / answer_status / latency_ms）  
- 规格：`docs/FEISHU_AGENT_V1.md`（Capacity 路线 + 权限入口 + 审计 P0）

## 下一刀（仍按你定的顺序）

④ 并发/超时/队列策略（建议先做：入口信号量 ≤2、队列、超时、429/Busy 语义）  
→ 再 ⑤ Feishu UX + Evidence Card  
→ ⑥ Canary  

**先不要**开质量小版本 / Wiki / ES / Memory。
