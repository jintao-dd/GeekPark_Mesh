# Mesh 轻量 Ontology（反推稿 · 非系统）

> **原则（锁定）**  
> Ontology schema **必须从现有对象反推**，不是先设计理想模型再改库 / Retrieval / Contract。  
> 价值一句话：**给 claim ↔ evidence 加语法**，不是把 Mesh 升级成知识图谱。

相关：`AGENT_QUALITY_V2.md` · Agent v1 Contract 冻结 · `app/agent/claim_support.py`。

---

## 执行边界

### 现在做

| 项 | 说明 |
|----|------|
| Ranking v1.4 | **已合入生产 rerank 并冻结** |
| Answer hard fail | A–H 归因；不改 Contract；不放宽 eval |
| Evidence | claim → support ∈ supported / insufficient / contradicted |
| **轻量 Ontology** | 整理已有对象的类型、字段、关系约束 |
| Gold | 用本 schema 描述 claim / support（`expect_support`） |

### 现在不做

Neo4j / KG · Graph RAG · 新 Retrieval path · 新 Tool · Planner / ReAct · LLM Wiki · Wiki 写入/自动维护 · 因 Ontology 改 Recall / Vector / Agent Contract · ES

### 冻结

Retrieval Recall · Vector · Agent Contract · Ranking（合入后） · Wiki / ES 后置

---

## Schema（仅文档化对齐；从现有对象反推）

| 类型 | 现有载体 | 关键字段 / 约束 |
|------|----------|----------------|
| **Entity** | item 标题/正文提及；`item_entity_facts` | 人 / 公司 / 产品名；无独立强约束表 |
| **Relation** | `published_json` relations · evidence[] | 双边主张；Published-only 可见 |
| **Claim** | `ClaimBinding.claim` · Answer 成文句 | 必须可绑定 EvidenceRef；确定性未来/完成态默认高门槛 |
| **EvidenceRef** | `ev:item:N` 等 | 指向已上线对象；**有 Ref ≠ 已支持 Claim** |
| **Team** | AskScope.team · owner_team | soft 信号，非 Ranking hard filter |
| **Event-Time** | `temporal.TimeSemantics`（basis/window） | unknown → 禁止「刚发生」；latest ≠ recent event |
| **Provenance** | issue status=published · published_json | **Published-only**；草稿/预览不得当真相 |

### Claim 记录模板（Gold / 评测）

```text
Claim {
  entity:     <string>          # 从 query/条目反推
  relation:   <string>          # mention | activity | speculative_certainty | …
  temporal:   <string>          # issue_time | future_certain | unspecified | …
  evidence_ref: [EvidenceRef]
  provenance: published_only
  support:    supported | insufficient | contradicted
}
```

### support 判定（最小）

| support | 含义 |
|---------|------|
| **supported** | Evidence 内容与 claim 的实体/关系/时间相容，且足以支撑该主张 |
| **insufficient** | 有或无 Evidence，但不足以支撑（含「主体相关 ≠ 支持确定性结论」） |
| **contradicted** | Evidence 明确否定 claim |

Temporal hard rules 与 Published-only **不变**：basis=unknown 时不得把 claim 说成墙上时钟「最近发生」。

实现入口：`app/agent/claim_support.py`（adapter + harness 共用；**不**新增 Tool / Retrieval path）。

---

## 与 Quality 线的关系

```
Truth (Published) → Claim → EvidenceRef → Support → Answer
        │
        ▼
Ranking 已收住「找什么」；本阶段钉「凭什么这么说」
```

不单独开「Ontology 系统」里程碑。

---

## Schema 对 Gold 的五个问题（收口，不扩建）

| 问题 | 当前结论 |
|------|----------|
| Claim 指向什么？ | query 意图 + 成文句；Gold 用 `claim_schema` / `expect_support` 描述 |
| Relation subject/object/type？ | Published relation 有双边；Ask 路径 claim 的 type 目前是粗粒度（`mention` / `speculative_certainty` / `universal_denial`） |
| EvidenceRef 如何证明 Claim？ | 必须经 support 判定；**有 Ref ≠ supported** |
| Temporal 挂哪？ | **Claim 约束**（`TimeSemantics`），不是独立 Event 实体——接受现状 |
| 哪些允许从 Evidence 推出？ | 仅 published；确定性完成/未来、全称否定默认 **不可** 从「主体相关」推出 |

若 Gold 暴露的是 support 漏检（如「已经量产上车」），**补判定规则**，不新建图数据库或 Ontology 系统。
