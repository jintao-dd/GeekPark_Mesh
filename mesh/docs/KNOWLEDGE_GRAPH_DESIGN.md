# Mesh Knowledge Graph v0 — 设计文档

> 版本：v0.1 · 2026-08-31  
> 阶段：**设计 + 最小 JSON 改造**（不改 pipeline、不上 Neo4j、不引入 Agent/ReAct）  
> 目标：补齐「关系资产沉淀」层，使关系卡可长期回溯原始材料。

---

## 0. 为什么这一步合理

| 对比 | Planner-lite / ReAct | Knowledge Graph v0 |
|------|----------------------|---------------------|
| 解决什么 | Ask 检索路径选择 | **关系资产不可追溯**（生产链固有问题） |
| 改动面 | Ask 编排 | draft/published JSON 字段扩展 |
| 与 Phase 1 关系 | 可并行，不互斥 | **先补资产，再让 Ask/Planner 消费** |
| 风险 | 架构漂移 | 低：不动 pipeline、不动 items/chunks/facts 表 |

Phase 1 已稳定 Ask 验收；KG v0 补的是 **周报生产链下游的资产层**，与「暂不 Agent 化」一致。

---

## 1. 三个核心对象

### 1.1 Entity（实体）

**语义**：公司、人物、团队、产品等可指称对象。

**与现有代码映射**：

| KG 字段 | 现有存储 | 文件 |
|---------|----------|------|
| `canonical_name` | `entities.name` UNIQUE | `schema_pg.sql`, `db.register_entities` |
| `kind` | `entities.kind`（默认 `auto`） | 发布时从 relations/contacts/keywords 抽取 |
| `aliases` | `entities.aliases` TEXT | 表已有，**使用不充分** |
| `first_issue` | `entities.first_issue` | 首次出现期号 |
| `roles` | `entities.roles` | 可选 |

**v0 JSON 形态（嵌入 Relation / Evidence，不强制独立表）：**

```json
{
  "entity_id": "ent:面壁智能",
  "canonical_name": "面壁智能",
  "aliases": ["面壁", "MiniMax 面壁"],
  "kind": "company",
  "mention_in_issue": "2026-8-17"
}
```

**Resolution 方案（v0 规则，无 LLM）：**

1. **精确匹配**：`entities.name` 或 `item.entities[]` 字符串相等  
2. **别名表**：`entities.aliases` JSON 数组（后续 editorial 维护）  
3. **标题解析**：`owner_guard._title_entities()` — 关系卡 title 中 `·` 分割  
4. **团队归一**：`ingest.canonical_team` / `qa_structured._normalize_team`  
5. **v0 不做**：跨语言 embedding 消歧、LLM 实体链接  

**Resolution 输出**：`{canonical_name, matched_via: "exact"|"alias"|"title_parse", confidence: 1.0|0.8}`

---

### 1.2 Relation（关系）

**语义**：两个或多个 Team/Entity 之间的可同步、可对比、可跟进关系（跨团队为主）。

**与现有代码映射**：

| KG 字段 | 现有字段 | 位置 |
|---------|----------|------|
| `relation_id` | **无** | v0 用 `{issue_slug}:{title_hash}` 生成 |
| `relation_type` | 隐含于 `label` | 「同一件事…」「一方接触…」 |
| `subject_entity` | `relations[].title` | draft/published JSON |
| `teams` | `relations[].teams[]` | 同上 |
| `confidence` | 隐含于 `weak` | `weak=false` → 高，`weak=true` → 低 |
| `status` | 隐含于 publish 流程 | draft → published；gate 拦截 |
| `narrative` | `body`, `details[]` | 展示层 |

**relation_type 枚举（v0）：**

| type | 触发 | 现有 label 示例 |
|------|------|-----------------|
| `cross_team_sync` | ≥2 团队同一实体 | 「同一件事，两个部门各知一半」 |
| `cross_team_gap` | 一方有、另一方可用 | 「一方接触，另一方用得上」 |
| `external_weak` | weak + 外部媒体 | LLM 保留的弱关系 |
| `unknown` | 兜底 | — |

**confidence / status：**

```json
{
  "confidence": 0.95,
  "status": "confirmed | weak | blocked | draft",
  "weak": false,
  "provenance_ok": true
}
```

- `provenance_ok`：来自 `build_relation_candidates` + `cross_team_provenance_ok`  
- `status=blocked`：`relation_gate.relation_publish_blockers` 拦截（未发布）

---

### 1.3 Evidence（依据）

**语义**：支撑一条 Relation 的原始材料引用（可追溯 item/source）。

**v0 字段（每条 evidence 记录）：**

```json
{
  "item_id": 12345,
  "source_id": 67,
  "team": "编辑部",
  "snippet": "与面壁智能詹杨帆讨论端侧模型…",
  "quote": "…",
  "source_label": "2026-8-17 · 编辑部例会",
  "pointer": "item:xxx",
  "observed_at": "2026-08-17"
}
```

**与现有代码映射**：

| 来源 | 字段 |
|------|------|
| `items` | `id`, `source_id`, `owner_team`, `text`, `source_label`, `pointer` |
| `relation_candidates.team_facts[]` | `team`, `item_ids`, `snippets`, `sources` |
| Ask analysis | `sources[].evidence[{ref, quote}]` — **独立 runtime 路径，v0 不对齐 DB** |

---

## 2. 当前代码映射（完整数据流）

```
items (entities, owner_team, source_id, text, pointer)
    │
    ├─► merge.apply_merge                          [merge.py]
    ├─► llm.build_team_card → cards.card_json       [preview_job, llm.py]
    │
    └─► build_relation_candidates                   [relation_candidates.py:41]
            │  candidate: {title, teams, team_facts[], item_ids[], provenance_ok}
            ▼
        llm.build_issue_draft(relation_candidates)  [llm.py:382, prompts/issue_draft*.md]
            │  LLM → relations[{label, weak, title, body, details, sources, teams}]
            ▼
        merge_relations_from_candidates               [relation_candidates.py:168]
            │  ★ 原问题：_candidate_item_ids 被 pop 丢弃
            │  ★ v0 修复：写入 item_ids + evidence[]
            ▼
        filter_draft_relations                      [owner_guard.py:219]
            ▼
        issues.draft_json
            │
            ├─► relation_publish_blockers             [relation_gate.py]
            └─► publish → published_json
                    ├─► register_entities           [db.py — title → entities]
                    ├─► snapshot_published_items
                    └─► reindex_issue
                            ├─► entity_team_facts (section=关系, 无 item_id)
                            ├─► search_fts
                            └─► item_facts / chunk_index

Ask（间接消费）：
  qa_structured section=关系 → entity_team_facts
  hybrid/FTS → search_fts「可同步的关系」
  **不读** relations[].evidence（v0 缺口，Phase 2+）
```

### 2.1 关键文件索引

| 文件 | 职责 |
|------|------|
| `app/relation_candidates.py` | 候选构建、merge、skeleton |
| `app/relation_gate.py` | 发布前 blockers |
| `app/owner_guard.py` | provenance 校验、filter |
| `app/preview_job.py` | draft 生成主流程 |
| `app/main.py` | `_build_draft_for_issue`, publish |
| `app/db.py` | `register_entities`, `reindex_entity_facts` |
| `app/qa_structured.py` | Ask structured 关系 section |

### 2.2 evidence 丢失点（改造前）

| 步骤 | 丢失内容 |
|------|----------|
| `_facts_detail` | 每 team 只保留第一条 snippet → `details[]` 字符串 |
| LLM rewrite | `team_facts` 整体未进入 draft |
| **`merge_relations_from_candidates:197`** | **`_candidate_item_ids` pop** |
| `reindex_entity_facts` | 扁平 snippet，无 item_id 列（**v0 不改**） |

---

## 3. 中间层：candidate → relation → card

### 3.1 目标分层

```
relation_candidate          Relation 资产（JSON）           Card 展示层
─────────────────          ─────────────────────          ──────────────
build_relation_candidates  relations[] + 新增字段          issue.html 渲染
  title                      relation_id (derived)          label, title, body
  team_facts[]               subject_entity                 details[] (人读)
  item_ids[]                 teams[], relation_type         sources[] (人读)
  provenance_ok              confidence, status
                             evidence[]
                             item_ids[] (denormalized)
```

### 3.1 原则

- **Card** = 展示层（`label/body/details/sources/teams`），读者可见  
- **Relation 资产** = Card + `evidence[]` + `item_ids[]` + 元数据（**v0 同一条 JSON 对象**）  
- **Candidate** = 生成期中间态，**不持久化**（仅在 LLM prompt 与 merge 时使用）

### 3.2 v0 JSON Schema（`published_json.relations[]` 扩展）

```json
{
  "label": "同一件事，两个部门各知一半",
  "weak": false,
  "title": "面壁智能",
  "body": "编辑部、商业化团队本期均有…",
  "details": ["编辑部记录：…", "商业化团队记录：…"],
  "sources": ["2026-8-17 · 编辑部例会", "…"],
  "teams": ["编辑部", "商业化团队"],

  "item_ids": [101, 205, 312],
  "evidence": [
    {
      "item_id": 101,
      "source_id": 3,
      "team": "编辑部",
      "snippet": "与面壁智能讨论…",
      "source_label": "2026-8-17 · 编辑部例会",
      "pointer": "…"
    }
  ],
  "provenance_ok": true,
  "relation_type": "cross_team_sync",
  "confidence": 0.95,
  "status": "confirmed"
}
```

**向后兼容**：`issue.html` 忽略未知字段；旧 published_json 无 `evidence` 仍正常展示。

---

## 4. 未来消费：Ask × 周报

### 4.1 共用资产层（不动 items/chunks/facts）

```
                    ┌─────────────────────────┐
                    │  Entity (canonical)      │
                    └───────────┬─────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
        items.entities    entity_team_facts   relations[].evidence
        (raw 条目)         (Ask structured)    (KG v0 新增)
              │                 │                 │
              └────────────┬────┴────────────────┘
                           ▼
                    Evidence (item_id → item/snapshot)
```

### 4.2 周报路径（不变 pipeline）

1. items → `build_relation_candidates`  
2. LLM draft → merge → **relations[] 含 evidence**  
3. publish → `published_json` 全量保留  
4. `reindex_entity_facts` 仍写扁平 snippet（Phase 2 可选 enrich）  
5. UI 可选：点击 relation → 展开 evidence 列表（Phase 2）

### 4.3 Ask 路径（Phase 2+，v0 仅设计）

| 场景 | 现状 | KG 增强 |
|------|------|---------|
| 「两边同时跟进谁」 | hybrid + entity_team_facts | + 读 `published_json.relations` evidence |
| structured 关系 section | SQL entity_team_facts | + item_ids 作 seed boost |
| follow-up | context_refs.item_ids | + 从 relation evidence 注入 |
| analysis verify | runtime sources[].evidence | + 与 KG evidence 交叉校验 |

**v0 不要求 Ask 改代码**；Planner-lite 可在 Phase 2 决定「读 facts vs 读 relation evidence」。

---

## 5. 最小改造方案（Phase 1 实施）

### 5.1 代码改动（仅 `relation_candidates.py`）

1. 新增 `_build_evidence(cand, items_by_id) -> list[dict]`  
2. 新增 `_attach_relation_assets(rel, cand, items_by_id)`  
3. `merge_relations_from_candidates`：merge 后 **attach**，**删除 pop**  
4. `skeleton_relation`：移除 `_candidate_item_ids` 临时字段（改由 attach 统一写）

### 5.2 不改

- pipeline 25 步  
- DB schema  
- `reindex_entity_facts`  
- Ask 路径  
- issue.html（额外 JSON 字段自动忽略）

### 5.3 测试

- 扩展 `tests/test_relation_candidates.py`：assert merge 后 `item_ids` + `evidence` 非空  
- 可选：snapshot draft_json relations[0] 字段

---

## 6. 后续 Phase 规划

| Phase | 内容 | 不改 |
|-------|------|------|
| **v0（当前）** | 设计文档 + JSON evidence 保留 | pipeline, Neo4j, Ask |
| **v0.1** | UI：关系卡展示 evidence 条数 / 跳转 item | — |
| **v1** | `entities.aliases`  editorial 维护 + resolution API | — |
| **v1.5** | Ask 读 `published_json.relations[].evidence` 作 seed | ReAct |
| **v2** | 独立 `relations` 表（可选）或 issue 级 relation index | 可仍无 Neo4j |
| **v3** | Planner-lite 消费 relation_type + evidence | Full Agent |

### 与 Planner-lite 的衔接

```
plan_retrieval(q)
  ├─ set_op needed? → qa_structured (现有)
  ├─ entity in relations? → 读 published_json.relations[].evidence (v1.5)
  └─ else → hybrid retrieve (现有)
```

KG v0 **先行**，Planner **后接** — 顺序正确。

---

## 7. 限制确认（本阶段）

- ❌ Neo4j / 图数据库  
- ❌ ReAct / Tool Calling  
- ❌ LLM Planner  
- ❌ pipeline 重构  
- ❌ 大规模 DB migration  
- ✅ draft_json / published_json 携带 evidence  
- ✅ 设计 relation 中间层  
- ✅ 文档化 Ask/周报共用路径  

---

## 附录 A：relation_candidate 示例

```json
{
  "title": "面壁智能",
  "teams": ["编辑部", "商业化团队"],
  "weak": false,
  "suggested_label": "同一件事，两个部门各知一半",
  "team_facts": [
    {
      "team": "编辑部",
      "item_ids": [101, 102],
      "snippets": ["与面壁智能詹杨帆讨论端侧模型…"],
      "sources": ["2026-8-17 · 编辑部例会"]
    }
  ],
  "item_ids": [101, 102, 205],
  "provenance_ok": true
}
```

## 附录 B：与 Ask Analysis evidence 的区别

| 维度 | KG Evidence | Ask `sources[].evidence` |
|------|-------------|---------------------------|
| 生命周期 | 发布期沉淀，跨期累积 | 单次 analysis runtime |
| 存储 | published_json | ask_analyses.sources_json |
| 用途 | 周报资产、未来 Ask seed | 当轮 verify/citation |
| v0 | 打通 JSON 保留 | 已在 Phase 1 SSE 持久化 |

两者 **互补**，v0 不要求合并。
