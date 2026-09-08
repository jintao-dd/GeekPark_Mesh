# Mesh + Agent 全量验收（当前主任务）

> **状态锁定（2026-09-08）**  
> ①～⑥ 架构冻结 ✅  
> ⑦ Agent v1 实现 + 真实 Adapter：**20/20 PASS** ✅（不再改 ⑦ 架构）  
> **全量验收 ← 当前主任务**  
> ⑧ 飞书 MVP：**暂缓 ⛔**（全量通过后只接线）

报告模板见 `eval/reports/MESH_AGENT_FULL_ACCEPTANCE.md`（执行中填写）。  
契约依据：`eval/reports/AGENT_V1_CONTRACT_20260908.md`。

---

## 0. 原则（硬）

不只测「正常能不能答」。必须大量测：

- 出错时会不会**答错 / 越权 / 串期 / 串团队 / 读到 draft**
- 会不会把**失败包装成成功**

目标：证明 Organization → Identity → Permission → Context → Published → Evidence → Tool Contract  
在**真实压力与异常**下仍然成立。

---

## 1. 七块验收清单

### 1. 全链路流程

```
Source → Pipeline → Preview → Relation → Claim → Publish
       → Index → Ask → Agent
```

| 检查项 | 通过标准 |
|--------|----------|
| 各阶段可观测 | 状态机 / job / draft vs published 边界清晰 |
| Publish 后 Index | Ask/Agent 只见 published |
| Agent 接同一 Published | Evidence 可回溯到期次 |

### 2. Agent 正确性

Identity / Permission / Context / Intent / Tool / Evidence / Answer  

依据：Agent 20/20 + 异常身份矩阵（conflict / unlinked / missing / 群视角）。

### 3. 数据安全

| 攻击面 | 期望 |
|--------|------|
| Draft / Raw / Unpublished | 拒绝；无内容泄漏 |
| Issue bypass | Tool 拒绝 |
| Team bypass | Tool 拒绝 |
| Permission bypass | ACL deny |
| Published-only | 恒成立 |

### 4. 回归（不得退化）

| 项 | 命令 / 依据 |
|----|-------------|
| Ask 25/25 | `eval/run_final_eval.py --corpus golden`（tmesh/prod 另跑） |
| Relation Gold | `pytest tests/test_relation_gold_*` |
| Claim Check | `pytest tests/test_relation_claim_*` |
| T13 | `pytest tests/test_t13_segment_quality.py` |
| Published write boundary | `pytest tests/test_publish_boundary.py tests/test_published_write_boundary.py` |
| Agent 20/20 | `pytest tests/test_agent_v1_contract.py` |

### 5. 性能

Pipeline / Preview / Publish / Ask / Agent → **P50 / P95**；记录 Agent 相对 Ask 的额外延迟。

### 6. 容错

LLM timeout、Tool timeout、空结果、Issue 异常、Identity conflict、Permission failure、DB / Redis / queue 异常 → **失败可见，不越权，不伪造成功**。

### 7. 并发

多人 Ask、多人 Agent、Preview+Agent、LLM pool 满、同用户连续、不同用户 Context 隔离（scope_key）。

---

## 2. 通过后

```
⑦ Agent v1          ✅ 20/20
        ↓
⭐ Mesh + Agent 全量验收
        ↓
通过
        ↓
⑧ 飞书 MVP（只接线，不扩大脑）
```

⑧ 形态：飞书事件 → open_id → Identity → … → Answer → 飞书回复。  
禁止塞入 Planner / ReAct / 多 Tool / Memory。

---

## 3. 执行入口

```bash
cd mesh
# 本地回归块（§4 子集，可先跑）
python eval/run_mesh_agent_full_acceptance.py --phase local_regression

# 完整报告路径（人工 + 远程补齐 perf/容错/并发后更新）
# eval/reports/MESH_AGENT_FULL_ACCEPTANCE.md
```
