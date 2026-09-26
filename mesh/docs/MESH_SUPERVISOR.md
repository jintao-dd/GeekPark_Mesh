# MeshSupervisor + Workers（Colleague 控制面）

> 主路径：`MESH_SUPERVISOR=1`（默认）→ [`supervisor/`](../app/agent/supervisor/)  
> 回滚：`MESH_SUPERVISOR=0` → 旧 `colleague_v3`；`MESH_COLLEAGUE_V3=0` → 更旧 Controller（仅紧急）。

## 能力归层

| 层 | 负责 |
|----|------|
| Wire | 飞书 I/O、卡片；thinking card 订阅 Supervisor `progress` |
| Policy | Identity / ACL / UAT |
| Session | goal / pending_write / turns |
| CompanyPrior | Ontology + Wiki + 飞书子树同事名（非 FACT） |
| **MeshSupervisor** | 规划、派工、预算、验真、重规划、唯一对外合成、WriteGate |
| Workers | Org / Research / Calendar / Published / CRM / Writer — 只执行 |

## 单轮循环（有预算）

1. Understand（CompanyPrior + person_resolve + Policy 工具面）  
2. Plan → `TaskGraph`（`plan_turn`，禁止 Decide+Planner 双脑）  
3. Assign / Observe → Workers → `TieredEnvelope`  
4. Verify → 可 `want_replan`（工具或参数变化才重跑）  
5. Mouth → 唯一用户可见文  
6. WriteGate：`prepare_write` 只存 `pending_write`；`confirm_write` 走 **Writer Worker** `run_step`

进度：`supervisor.progress.emit_progress` → `feishu_bot` 热更新 thinking card。

## 分桶

`published` | `feishu_live` | `wiki_prior` | `analysis` — FACT 不得跨桶冒充。

## 禁止

- 关键词穷举用户话术当产品主路径
- Decide + Planner 双脑并行（已由 Supervisor 单规划替代）
- 子 Agent 对用户独立人格说话
- 把 Controller / 正则 Complexity 当主路径继续加需求
- WriteGate 再 monkey-patch `colleague_v3._decide`
