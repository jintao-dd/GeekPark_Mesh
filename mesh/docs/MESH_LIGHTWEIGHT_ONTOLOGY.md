# Mesh 轻量 Ontology（反推稿 · 非系统）

> **2026-09-11：** Colleague 轨另建 **Company Ontology**（懂组织结构），见 `COLLEAGUE_AGENT_V4.md` §1.5。本文仍只服务质量轨 claim↔evidence **语法**；两层勿混。  
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
  strength:   weak | strong     # 是否断言「已完成/发生/成立」的现实状态
  assertion_type: mention | completed_state | universal_denial | …
  support:    supported | insufficient | contradicted
}

Evidence {
  … 
  entailment_to_claim: entailing | direct_related | topical | none | contradicted_by_evidence
  # direct_related ≠ 足以 supported；topic ≠ entailment
}
```

### support 判定（最小）

| support | 含义 |
|---------|------|
| **supported** | Evidence **语义蕴含** claim（不仅 topic/entity overlap）；强 claim 须 entailing |
| **insufficient** | 有或无 Evidence，但不足以支撑（含「主体相关 ≠ 支持确定性结论」；strong + topical/direct_related） |
| **contradicted** | 明确 published 反证；找不到支持 ≠ contradicted |

> v2.4c：三维度分判 `claim_strength` / `evidence_entailment` / `counter_evidence`（见 `app/agent/claim_semantic_ext.py` Shadow）。**不**扩 Event/Relation schema。

Temporal hard rules 与 Published-only **不变**：basis=unknown 时不得把 claim 说成墙上时钟「最近发生」。

实现入口：`app/agent/claim_support.py`（生产）· `app/agent/claim_semantic_ext.py`（v2.4c Shadow；Gate 接入前不影响 Answer）。**不**新增 Tool / Retrieval path。

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

### Claim strength / Support（v2.3e 收口语义）

```
strong claim  ≠  topic/entity-related evidence
  → 无直接支持该结论的 evidence 时：support = insufficient
  → 不得因 overlap 升级为 supported

contradicted
  → 需要明确 published 反证（如「从未接触」vs「已接触」）

insufficient
  → 不能仅凭相关证据升级为 supported
  → Answer 层可对 insufficient|contradicted 执行 abstain（safety ≠ label）
```

不增加 Relation type 爆炸、Graph、KG、Neo4j。
