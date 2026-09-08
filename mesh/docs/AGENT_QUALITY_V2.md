# Agent Quality / v2

> **状态（2026-09-08 Overnight）**  
> Agent v1 = **GO**  
> **① Temporal** = 历史 24/24；本晚重跑 **23/24**（T19）— 不改架构  
> **② Recall** = **冻结 83/90/94**；Vector A/B **回退**（−6.7pp，不接主线）  
> **③ Ranking** = Phase0/1 闭环；v1 宏↑但 R06/R13 误杀 → **不合并生产**  
> **④ Evidence / ⑤ Answer** = baseline 已冻结（见 Overnight 报告）  
> 报告：`eval/reports/AGENT_QUALITY_V2_OVERNIGHT.md`  
> **原则：先可测量，再优化；失败保留 baseline；不改 v1 Contract。**

相关：`MESH_AGENT_FULL_ACCEPTANCE.md` · `TEMPORAL_PHASE1.md` · `RECALL_FAILURE_REVIEW.md`  
⑧ 飞书 MVP ∥ 质量线（只接线）。

---

## 正式状态板

```
v1                         ✅ GO

① Temporal                 ✅ 24/24 历史 / ⚠️ 本晚 23/24 (T19)
  Hard Rules 已通过

② Retrieval Recall
  Phase 0                   ✅ PASS · 74/79/79
  Failure Review             ✅ PASS
  Phase 1                   ✅ 冻结 83/90/94
  Vector A/B                ❌ 不接（FTS+Vec 76/83/88）

③ Ranking                   ✅ 测量闭环
  Baseline                  MRR 0.807 / nDCG@10 0.825 / P@5 0.511
  Exp v1                    +0.056 / +0.037 / +0.013 · 有误杀 → 不合并

④ Evidence                  ✅ baseline 冻结（cov/corr/cite=1.0）
⑤ Answer                    ✅ baseline 冻结（pass 93% · acc 0.73）
⑥ Data Domain               🔒 LOCKED

统一 Runner                 ✅ eval/run_quality_v2.py
Overnight                   ✅ AGENT_QUALITY_V2_OVERNIGHT.md
```

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
