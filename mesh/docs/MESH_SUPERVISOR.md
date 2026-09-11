# MeshSupervisor + Workers（Colleague 控制面）

> 主路径：`MESH_SUPERVISOR=1`（默认）→ [`supervisor/`](../app/agent/supervisor/)  
> 回滚：`MESH_SUPERVISOR=0` → 旧 `colleague_v3`；`MESH_COLLEAGUE_V3=0` → 更旧 Controller（仅紧急）。

## 能力归层

| 层 | 负责 |
|----|------|
| Wire | 飞书 I/O、卡片 |
| Policy | Identity / ACL / UAT |
| Session | goal / pending_write / turns |
| CompanyPrior | Ontology + Wiki（非 FACT） |
| **MeshSupervisor** | 规划、派工、预算、验真、重规划、唯一对外合成 |
| Workers | Org / Research / Calendar / Published / Writer — 只执行 |

## 分桶

`published` | `feishu_live` | `wiki_prior` | `analysis` — FACT 不得跨桶冒充。

## 禁止

- 关键词穷举用户话术当产品主路径
- Decide + Planner 双脑并行（已由 Supervisor 单规划替代）
- 子 Agent 对用户独立人格说话
- 把 Controller / 正则 Complexity 当主路径继续加需求
