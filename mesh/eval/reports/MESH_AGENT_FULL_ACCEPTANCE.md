# Mesh + Agent 全量验收报告

> 更新：2026-09-08  
**总 verdict：🟡 未结案** — Layer 1 ✅；Layer 2 🟡（Publish 边界 local 已过，tmesh 全链路与 LLM E2E 仍缺）；Layer 3 ⬜；Layer 4 ⛔

| 层 | 含义 | 状态 |
|----|------|------|
| Layer 1 | 契约 / 本地回归 | ✅ 已通过 |
| Layer 2 | 真实数据 E2E（全链路 + Agent LLM） | 🟡 Publish 边界 local ✅；tmesh 全链路 / LLM E2E ⬜ |
| Layer 3 | 性能 / 压力 / 故障 / 并发 | ⬜ 待补 |
| Layer 4 | 飞书 E2E | ⛔ ⑧ 之后 |

⑦ Agent v1：**✅ 20/20**（不再改架构）  
⑧ 飞书：**⛔ 暂缓**

---

## Layer 1 — 契约（已锁死）

| 项 | 结果 |
|----|------|
| Agent 20/20 | PASS |
| Ask 25/25 golden | PASS |
| Relation Gold | PASS |
| Claim Check | PASS |
| T13 5/5 | PASS |
| Publish / write boundary | PASS |
| Identity / Permission / Tool 自防御 | PASS（含于 Agent 契约） |

执行：`python eval/run_mesh_agent_full_acceptance.py --phase local_regression`（2026-09-08，6/6）。

---

## Layer 2 — 真实数据 E2E

### 2.1 全链路 / Publish 前后可见性

| 检查 | 环境 | 结果 |
|------|------|------|
| Publish **前** Agent 不可见 draft 标记 | local | ✅ `AGENT_PUBLISH_BOUNDARY_E2E.json` |
| Publish **后** Agent 可见 + Evidence | local | ✅ |
| Source→Pipeline→Preview→Relation→Claim→Publish（真实 tmesh Issue） | tmesh | ⬜ 下一刀 |

脚本：`eval/run_agent_publish_boundary_e2e.py`（本地已绿；tmesh 用 `--reuse-env-db` 另跑）

### 2.2 Agent LLM E2E（10～20 题）

| 检查 | 结果 |
|------|------|
| MESH_AGENT_USE_LLM=1 成文 | ⬜ |
| Answer 遵守 Evidence | ⬜ |
| unsupported 不上屏为事实 | ⬜ |
| fingerprint / trace | ⬜ |

语料：`eval/agent_e2e_gold_v1.jsonl` · 脚本：`eval/run_agent_llm_e2e.py`

---

## Layer 3 — 性能 / 故障 / 并发

### 性能 P50/P95

| 路径 | P50 | P95 | n | 环境 | 备注 |
|------|-----|-----|---|------|------|
| Pipeline（Job 完成） | | | | | 非仅 HTTP |
| Preview（可进入/完整） | | | | | |
| Publish | | | | | |
| Ask | | | | | |
| Agent | | | | | |
| **Agent − Ask overhead** | | | | | |

### 故障（运行时，非仅契约）

| 场景 | 结果 |
|------|------|
| LLM timeout / bad JSON / 慢响应 | ⬜ |
| Tool timeout / 空结果 / 异常 | ⬜ |
| DB / Redis / queue | ⬜ |
| unlinked / conflict / team·issue 非法 | 部分✅契约；运行时复测 ⬜ |

### 并发（防 Context 串用户）

| 场景 | 结果 |
|------|------|
| A 商业化+编辑部群 vs B 视频号 同时问「我们这周接触了谁」 | ⬜ |
| 多人 Ask / Agent / Preview+Agent / pool 满 | ⬜ |

---

## Layer 4 — 飞书 E2E

⛔ 全量 Layer 2+3 通过后再开。只接线，不扩大脑。

---

## Go / No-Go

- [x] Layer 1  
- [ ] Layer 2  
- [ ] Layer 3  
- [ ] → 全量 PASS  
- [ ] → ⑧ 飞书 MVP  
