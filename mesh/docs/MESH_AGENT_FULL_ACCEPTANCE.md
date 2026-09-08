# Mesh + Agent 全量验收（当前主任务）

> **状态锁定（2026-09-08）**  
> ①～⑥ 架构冻结 ✅  
> ⑦ Agent v1 + 真实 Adapter：**20/20** ✅（**不再改 ⑦ 架构**）  
> 全量验收：**🟡 未结案**（功能契约层已过；Layer 2～3 待补）  
> ⑧ 飞书 MVP：**⛔ 暂缓**

报告：`eval/reports/MESH_AGENT_FULL_ACCEPTANCE.md`  
⑦ 证据：`eval/reports/AGENT_V1_CONTRACT_20260908.md`

---

## 四层门禁（最终报告结构）

```
Layer 1  契约 / 本地回归          ✅ 已通过
Layer 2  真实数据 E2E             ⬜ 待补（当前最高优先级）
Layer 3  性能 / 压力 / 故障       ⬜ 待补
Layer 4  飞书 E2E                 ⛔ ⑧ 之后
```

**禁止**因 Layer 1 PASS 就开飞书。

---

## Layer 1 — 已锁死（充分证据）

| 项 | 状态 |
|----|------|
| ①～⑦ 架构 | ✅ |
| Agent 契约 20/20 | ✅ |
| 真实 Ask / Relation adapter | ✅ |
| Ask Golden 25/25 | ✅ |
| Relation Gold / Claim Check / T13 / Publish boundary | ✅ |
| Identity / Permission / Issue·Team / Tool 自防御 | ✅ |

本地执行器（**仅 Layer 1，不扩成万能脚本**）：

```bash
python eval/run_mesh_agent_full_acceptance.py --phase local_regression
```

性能 / 远程故障 / 并发用**独立脚本**，结果汇总进同一报告。

---

## Layer 2 — 真实数据 E2E（下一刀）

### 2.1 全链路（tmesh 至少 1 次）

```
Source → Pipeline → Preview → Relation → Claim
       → Publish → Index → Ask → Agent
```

**硬验收：** Publish **前** Agent 查不到新数据；Publish **后** 才能查到。  
验证的是真实数据生命周期，不是单元测试。

独立脚本：`eval/run_agent_publish_boundary_e2e.py`

### 2.2 Agent 真实 LLM E2E

契约 20/20 ≠ 最终回答质量已验。须补 **10～20 题** Agent E2E Gold：

```
问题 → Agent → 真实 Tool → Evidence → 真实 LLM → Answer
```

检查：Answer 是否守 Evidence；unsupported 是否不上屏为事实；fingerprint / trace 仍在。

语料骨架：`eval/agent_e2e_gold_v1.jsonl`（逐步填满）  
执行器：`eval/run_agent_llm_e2e.py`（`MESH_AGENT_USE_LLM=1`）

---

## Layer 3 — 性能 / 故障 / 并发

### 性能

测 Job 完成语义（点击 → Job 完成 / Preview 可进 / Preview 完整），不只 HTTP 返回。  
记录 Pipeline / Preview / Publish / Ask / Agent 的 **P50/P95**，以及 **Agent − Ask overhead**。

### 故障

LLM timeout / bad JSON / 慢响应；Tool timeout / 空结果；DB·Redis·queue；身份异常。  
期望：**失败可见、不伪造成功、不越权、不串上下文、不泄漏 draft**。

### 并发

重点不是 QPS，而是 **Context 不串用户**（对照 A 商业化/编辑部群 vs B 视频号）。

独立脚本（后续）：`eval/run_agent_perf.py` / `eval/run_agent_fault.py` / `eval/run_agent_concurrency.py`

---

## 推荐执行顺序（不要乱）

1. tmesh 全链路 + Publish 前后 Agent 可见性  
2. Agent 真实 LLM E2E（10～20）  
3. 性能 P50/P95 + overhead  
4. 故障注入  
5. 并发 / Context 隔离  
6. 更新 FINAL / 本报告 → Go/No-Go  
7. 通过 → ⑧ 飞书 MVP（只接线）

---

## 通过后

```
⑦ ✅ → Layer2+3 全绿 → ⑧ 飞书接线（不扩 Planner/ReAct/多 Tool/Memory）
```
