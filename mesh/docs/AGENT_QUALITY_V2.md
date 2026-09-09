# Agent Quality / v2

> **状态（2026-09-09 · Ranking v1.4 候选）**  
> Temporal **24/24** · Recall **83/90/94** 冻结 · Vector **OFF**  
> Ranking v1.4：****4125→Top5** · worse=[] · R06/R16/R18/R@20 守住 → **CANDIDATE 合并（待确认）**  
> 轻量 Ontology = 反推 schema only · Wiki / Agentic RAG **冻结**  
> 报告：`AGENT_QUALITY_V2_3b_RANKING_4125.md`

相关：`MESH_AGENT_FULL_ACCEPTANCE.md` · `TEMPORAL_PHASE1.md` · `RECALL_FAILURE_REVIEW.md`  
⑧ 飞书 MVP ∥ 质量线（只接线）。

---

## 正式状态板

```
v1                         ✅ GO
Overnight / v2.1 / v2.2    ✅ 收口

① Temporal                 ✅ 24/24
② Retrieval Recall         ✅ 冻结 83/90/94 · Vector OFF
③ Ranking-adjusted         🟡 v1.2 实验（R16 挡合并）→ v2.3 攻 4125/R16
④ Evidence                 🟡 对抗题生效 · claim/support 语法化
⑤ Answer                   🟡 hard pass ~88% · 继续清 fail
⑥ Data Domain              🔒 LOCKED

轻量 Ontology              📌 反推稿（非系统）MESH_LIGHTWEIGHT_ONTOLOGY.md
LLM Wiki                   ⛔ 延后（Feishu 后评估 · 仅派生视图）
```

### 执行边界（最终锁定）

**Ontology schema 必须从现有对象反推**（item_facts / Relation / EvidenceRef / Entity / team / event_time），  
禁止「理想 Agent 模型 → 改库 → 改 Retrieval → 改 Contract」。

| 现在做 | 现在不做 |
|--------|----------|
| v2.3 Ranking：4125 / R16 | Neo4j / KG / Graph RAG |
| Answer 逐题 hard fail | 新 Retrieval path / 新 Tool |
| Evidence：claim→support 判定 | Planner / ReAct |
| 轻量 Ontology：整理已有 schema | LLM Wiki / Wiki 写入 |
| Gold 用 schema 描述 claim/support | 因 Ontology 改 Recall / Vector / Contract |

以后：Quality 稳定 → Feishu Agent → 再评估 Wiki。

---

## Failure Review → 四线（锁定）

| 线 | Cases | 归属 | 说明 |
|----|--------|------|------|
| **Scope / Product** | R02 / R23 / R24 | **✅ 语义已落地** | 从 Recall 缺陷名单移除；现为 Scope 产品验证样本 |
| **Retrieval Recall 候选** | R11→4106 · R12→4125 · R13→4154–4156 · R18→4271 | Phase 1 FTS/Query | 共 7 个 missed relevant |
| **Ranking** | R12 → 4120@6 / 4128@9 | **③** | **不能拿来抬 Recall** |
| **Data / Index** | R14 → 4408–4412 | ✅ 核查完 → **A** | 已进 item_facts/chunk；原句 miss → 归 FTS/Query |

---

## ② Retrieval Recall Phase 1 — 锁定顺序

```
① Scope：落实 recent 可跨已发布 Issue     ✅
        ↓
② R14：先核查 Index/Data                  ✅ → 归因 A（进索引，原句未召回）
        ↓
③ FTS/Query：R11/R12(4125)/R13/R18 + R14  ← NEXT
        ↓
④ 重跑 Retrieval Gold
        ↓
⑤ Recall 达标
        ↓
⑥ 才进入 Ranking
```

### ① Scope — 已锁定并落地

**产品语义（正式）：**

```
IssueRef   = latest_published   # 当前上下文 / 锚定期次
TimeWindow = recent             # 要找的时间范围
二者正交
```

因此：

```
「锦涛最近做了什么」
→ IssueRef = latest_published（当前期）
→ TimeWindow = recent
→ Retrieval 可以跨已发布 Issue
→ 只返回符合 recent 时间约束的证据
```

而不是：`latest_published → 只搜当前 Issue → 找不到 → 判定没有最近内容`。

**边界（不变）：**

| | |
|--|--|
| ✅ | 允许跨 Issue |
| ✅ | 仅限 published |
| ✅ | TimeWindow 继续由 Temporal 层控制 |
| ❌ | 不读 draft/raw |
| ❌ | 不因为「最近」突破权限 / Scope |
| ❌ | 不改变 v1 Tool Contract |

**实现要点：**

- `temporal.resolve_filter_mode`：`latest_published` + `recent|none` → `filter_mode=time_window`
- `adapters.scope_from_agent`：`time_window` 时 **清空检索 slug**；Context `issue_ref` 不变
- `explicit` / `pinned` / `本周|上周` → 仍 `issue_anchor`
- R02 / R23 / R24 **不再计入 Recall 缺陷**

### ② R14 Index / Data — ✅ DONE

详见 `eval/reports/R14_INDEX_CHECK.md`。

```
4408–4412 → 全部在 items + item_facts + chunk_index（published · 2026-09-08）
search_fts 正文轨未收录（仍属 item 轨可检索）
「视频号 播放」可命中；R14 原句 hybrid 只见 4445
→ 结论 A：已进索引，但检索没召回 → 正式进入 ③ FTS/Query
```

### ③ FTS / Query（真正的 Recall 改动面）

| Case | Missed items |
|------|----------------|
| R11 | 4106 |
| R12 | 4125（仅此；4120/4128 属 Ranking） |
| R13 | 4154–4156 |
| R18 | 4271 |
| **R14** | **4408–4412**（Index 已证 A，可进） |

### Vector（可选对照 · 不叫「优化」）

baseline = `Embed False`。可单独 FTS-only vs FTS+Vector 对照，不接主线。

### 明确禁止（Phase 1）

- ❌ 因 R12 的 4120@6 / 4128@9 提前上 Rerank / 开 ③  
- ❌ 把 Scope 样本算成 FTS 缺陷  
- ❌ 未核 R14 索引就改 Recall  
- ❌ 一次改 Scope + Index + FTS + Ranking  
- ❌ 盲目接 Vector 当优化  

---

## ① Temporal PASS — 已解决 / 不负责

**已解决：** relative time intent · time basis · time filter ·  
`latest_published ≠ recent event` · unknown 不乱说「最近发生」

**不负责（不得混进 Temporal 回改）：**

| 遗留 | 归属 |
|------|------|
| latest 上搜不到内容 | **② Scope ✅ 已正交落地；余量归 Index/FTS** |
| 历史内容没被召回 | **② Retrieval Recall** |
| RAG 漏召回 | **② Retrieval Recall** |
| `2026-8-17` date_start/end 脏 | **Data Time Integrity**（数据） |
| 无真实 `event_time` | **诚实默认 unknown**（正确，不伪造） |

---

## 质量层对照

| 质量层 | 状态 |
|--------|------|
| 边界 / Tool / Identity / IssueRef | ✅ 保持（Contract 未改） |
| 时间语义 | ✅ Temporal PASS |
| Recall Phase 0 + Failure Review | ✅ PASS |
| Recall Phase 1 · Scope | ✅ DONE |
| Recall Phase 1 · Index / FTS | ⬜ NEXT |
| Ranking | 🔒 LOCKED |
| Evidence / Answer | 🔒 LOCKED |
| CRM / Data Domain | 🔒 LOCKED |

---

## Code 清单

1. ✅ Temporal PASS  
2. ✅ `eval/retrieval_gold_v1.jsonl`（30）  
3. ✅ `eval/run_recall_baseline.py` → macro R@5/10/20（已对齐 Scope 正交）  
4. ✅ **Failure Pattern Review**  
5. ✅ **Phase 1 Scope** — `temporal` + `adapters.scope_from_agent`  
6. ⬜ R14 Index 核查  
7. ⑧ 飞书可并行  
