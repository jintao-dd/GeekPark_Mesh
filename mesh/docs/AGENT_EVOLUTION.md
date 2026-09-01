# Agent 演进设计（基于现有架构，不重构）

> 2026-08-31 · Phase 3 Verify v2 收口 · **North Star 定稿**

---

## North Star（事实链优先，Agent 后置）

**目标流水线：**

```
Sources（唯一事实源）
  → Extract → items / entities / chunks
  → KG：Entity · Relation · Evidence
  → LLM 只负责组织表达（draft narrative）
  → Validator 证明「每句话可回溯」
  → Owner 审 weak / 交叉 / 缺口
  → Published
```

**硬约束：**

| 原则 | 含义 |
|------|------|
| **Sources 是唯一事实源** | 任何 Published 事实必须来自上传材料经抽取的 items/chunks |
| **LLM 只组织表达** | 归纳、排版、paraphrase；**不得创造**材料中不存在的关键事实 |
| **Published 必须可回溯** | `narrative → evidence[] → item_id/chunk_id → source_id → 原始材料` |
| **Agent 未来角色** | Entity / Relation / Evidence 之上的 **orchestrator**（检索、判断、组合、校验） |
| **Agent 不做** | 事实作者；**不替 Owner** 做最终 publish 决策 |

**反模式（禁止）：**

```
上传材料 + LLM 自由发挥 → Owner 碰运气检查
```

**现阶段优先级（Phase 3，不做 ReAct / Full Agent / Neo4j / LLM Planner）：**

1. Relation Verify v2 — narrative grounded 到 evidence
2. 扩展 Verify 至 lead / keywords / views / contacts 等 LLM 事实区块
3. Owner 闸门 — 无 evidence、跨团队无独立出处、unsupported narrative **默认不可 publish**
4. tmesh 全链路试跑 — preview → merge → verify → owner → publish（生产只读）

---

## 1. 设计原则

1. **先稳定验收，再 Agent 化** — 不替换 `ask_analysis.run_analysis` 固定流水线
2. **Planner-lite 优于 Full ReAct** — 检索路径选择用纯规则，不必 tool loop
3. **Verify 继续加强，Reflection 后置** — Validator 证明链优先于 Agent orchestration

---

## 2. 能力地图（Ask + 周报双轨）

### Ask 轨（已验收）

```
Query → Guard → Router → Planner-lite → RAG → Analysis → Verify/Citation → Answer
```

### 周报轨（Phase 3 目标）

```
Sources → Pipeline → Cards → build_issue_draft (LLM 组织)
  → merge_relations (Evidence 挂载)
  → relation_verify + issue_verify (Validator)
  → owner_guard + relation_gate (Owner 闸门)
  → Owner publish
```

---

## 3. Phase 3 字段盘点（LLM 生成 × evidence 约束）

| 字段 | 生成路径 | evidence 约束 |
|------|----------|---------------|
| `relations[].body/details` | `llm.build_issue_draft` → `merge_relations_from_candidates` | ✅ `relation_verify` + `evidence[]` |
| `relations[].evidence[]` | `relation_candidates._build_evidence` | ✅ item_id / source_id |
| `lead` | `llm.build_issue_draft` | ✅ `issue_verify` |
| `keywords.*.rows[].v` | `llm.build_issue_draft` | ✅ `issue_verify` |
| `plans.*.rows[].v` | `llm.build_issue_draft` | ✅ `issue_verify` |
| `views[].text` | `llm.build_issue_draft` | ✅ `issue_verify` |
| `contacts.*.rows`（要点等） | `llm.build_issue_draft` | ✅ `issue_verify` |
| `kpis` / `gaps` / `data_sources` | LLM / 模板 | ⏸ 计数或接入说明，owner 目视 |
| `question` | 模板固定 | N/A |

盘点 API：`app.issue_verify.issue_field_inventory()`

---

## 4. Verify v2 模块索引

| 组件 | 文件 | 作用 |
|------|------|------|
| Relation narrative | `relation_verify.py` | body/details ↔ relation.evidence[] |
| Issue 全稿区块 | `issue_verify.py` | lead/keywords/views/contacts ↔ items corpus |
| Owner 闸门 | `relation_gate.py` | weak / 无 evidence / 跨团队 provenance / unsupported |
| 归属硬约束 | `owner_guard.py` | 入库 + filter_draft_relations |
| Ask claim 绑定 | `ask_analysis.py`, `ask_citation.py` | 问答侧 evidence 证明 |

验收脚本：`deploy/run_phase3_acceptance.py`（tmesh，默认不 publish）

---

## 5. 里程碑

| 里程碑 | 状态 | 验收标准 |
|--------|------|----------|
| M1 稳定 | ✅ | golden/prod 检索 ≥22/25 |
| M2 Planner-lite | ✅ | e08/e10/e21 structured |
| **M3 Verify v2** | **✅ 本轮** | 周报 narrative + 多区块 verify + publish 闸门 |
| M4 Agent orchestrator | ⏸ | 等 M3 全链路 tmesh 试跑稳定后再定插入点 |

**仍不做**：ReAct、LLM Planner、Neo4j、Evidence schema 变更、Ask 大规模读 relations evidence。

---

## 6. 文件索引（历史）

| 阶段 | 文件 |
|------|------|
| Guard ✅ | `ask_query_guard.py`, `ask_engine.py` |
| Planner-lite ✅ | `ask_planner.py`, `ask_engine.py` |
| KG v0 evidence ✅ | `relation_candidates.py` |
| **Verify v2 ✅** | `relation_verify.py`, `issue_verify.py`, `relation_gate.py` |
| Eval | `eval/run_final_eval.py`, `deploy/run_phase3_acceptance.py` |
| 生产政策 | `docs/PROD_DATA_POLICY.md`, tmesh staging |

（Planner-lite 全链路验收数据见 git 历史 / `eval/reports/`）
