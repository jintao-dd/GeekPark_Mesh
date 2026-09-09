# Mesh 轻量 Ontology（反推稿 · 非系统）

> **原则（锁定）**  
> Ontology schema **必须从现有对象反推**，不是先设计理想模型再改库 / Retrieval / Contract。  
> 价值一句话：**给 claim ↔ evidence 加语法**，不是把 Mesh 升级成知识图谱。

相关：`AGENT_QUALITY_V2.md` · Agent v1 Contract 冻结。

---

## 执行边界

### 现在做

| 项 | 说明 |
|----|------|
| Quality v2.3 Ranking | 4125 Top5 / R16 不回退 |
| Answer 逐题清 hard fail | must_mention / abstention / 非空 |
| Evidence | claim → supporting evidence 判定说清 |
| **轻量 Ontology** | 整理已有对象的类型、字段、关系约束 |
| Gold | 开始用这套 schema 描述 claim / support |

### 现在不做

Neo4j / KG · Graph RAG · 新 Retrieval path · 新 Tool · Planner / ReAct · LLM Wiki · Wiki 写入/自动维护 · 因 Ontology 改 Recall / Vector / Agent Contract

### 以后做

Quality 稳定 → Feishu Agent → 再评估 Wiki（且必须是 **published 派生视图**）

---

## 反推对象清单（已存在 → 钉住）

| 对象 | 来源（现状） | 角色 |
|------|----------------|------|
| **Issue** | `issues` · published_json | 期次锚点 / Scope |
| **Item** | `items` | 已上线条目事实载体 |
| **item_facts** | `item_facts` / FTS | 检索与摘录单元 |
| **Entity** | item / facts 中的人·公司等提及 | 指称对齐（尚未独立表强约束） |
| **team** | AskScope / 条目归属 | soft 信号，非 hard filter（Ranking） |
| **event_time / Temporal** | `temporal.resolve_*` · basis/window | 能否说「最近/昨天」的语法 |
| **Relation** | published relations · evidence[] | 双边关系主张 |
| **EvidenceRef** | `ev:item:N` 等 | 证据指针 |
| **ClaimBinding** | Agent adapters | claim ↔ evidence 绑定（结构） |

### claim ↔ support（语法意图，非新引擎）

```
Claim 可被 Support，当且仅当：
  - 存在 EvidenceRef 指向 published 可见对象
  - 证据内容与 claim 的实体/关系类型相容（按现有 Relation/Item 语义）
  - Temporal：basis=unknown 时不得把 claim 说成墙上时钟「最近发生」

有 EvidenceRef ≠ claim 已被支持
  → Evidence Gold 对抗题继续覆盖
```

**禁止**：为补全 ontology 而改检索集合、改 Tool Contract、新建 Wiki 真相源。

---

## 与 Quality 线的关系

```
Ranking / Answer / Evidence 实验
        │
        ▼
Gold 用本文件 schema 描述 claim/support
        │
        ▼
仍分测：Retrieval ≠ Ranking ≠ Answer
```

不单独开「Ontology 系统」里程碑。
