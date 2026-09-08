# Agent Quality / v2

> **状态（2026-09-08）**  
> Agent v1 = **GO**  
> **① Temporal = PASS**（Gold 24/24 · Hard Rules 已通过）  
> **② Retrieval Recall**  
> · Phase 0 = **PASS**（Gold n=30 · Baseline 74/79/79 · Embed=False）  
> · Failure Review = **PASS**（四线分层完成）  
> · **Phase 1 = 🟡 READY**（按锁定顺序推进；禁止一次改四层）  
> **③ Ranking / ④ Evidence / ⑤ Answer / ⑥ Data Domain = 🔒 LOCKED**  
> **原则：先可测量，再优化；先证明问题，再改架构。**

相关：`MESH_AGENT_FULL_ACCEPTANCE.md` · `TEMPORAL_PHASE1.md` · `RECALL_FAILURE_REVIEW.md`  
⑧ 飞书 MVP ∥ 质量线（只接线）。

---

## 正式状态板

```
v1                         ✅ GO

① Temporal                 ✅ PASS
  Gold 24/24
  Hard Rules 已通过

② Retrieval Recall
  Phase 0                   ✅ PASS
  Gold n=30
  Baseline 74/79/79
  Failure Review             ✅ PASS

  Phase 1                   🟡 READY
  Scope 决策
  Index 核查
  FTS/Query 优化
  Vector 对照实验（可选）

③ Ranking                   🔒 LOCKED
④ Evidence                  🔒 LOCKED
⑤ Answer                    🔒 LOCKED
⑥ Data Domain               🔒 LOCKED
```

---

## Failure Review → 四线（锁定）

| 线 | Cases | 归属 | 说明 |
|----|--------|------|------|
| **Scope / Product** | R02 / R23 / R24 | 产品决策 | latest→2026-09-08，相关在 2026-8-17，0 hit；**不算 FTS 缺陷** |
| **Retrieval Recall 候选** | R11→4106 · R12→4125 · R13→4154–4156 · R18→4271 | Phase 1 FTS/Query | 共 7 个 missed relevant；逐条验 token→FTS→Query→Hybrid |
| **Ranking** | R12 → 4120@6 / 4128@9 | **③** | Top20 有、Top5 无；**不能拿来抬 Recall** |
| **Data / Index** | R14 → 4408–4412 | 先核查索引 | 确认没进索引后才决定是否进 Recall |

**已验证判断：** 不是「Recall 74% 所以赶紧改 RAG」，而是已能定位「哪些真是 Recall、哪些不是」。

---

## ② Retrieval Recall Phase 1 — 锁定顺序

> **不要一次改四层。顺序直接锁：**

```
① Scope / Product 决策
      ↓
② R14 Index / Data 核查
      ↓
③ FTS / Query Recall 优化（仅 R11 / R12(4125) / R13 / R18）
      ↓
④ 重跑 Retrieval Gold
      ↓
⑤ Recall 达标
      ↓
⑥ 开 ③ Ranking
```

### ① Scope（第一优先级 · 未定前不算 FTS 缺陷）

R02 / R23 / R24 共同特征：

```
latest → 2026-09-08
相关内容在 2026-8-17
当前检索 0 hit
```

**待锁定产品语义：**

> 「最近 / 近期」是否允许跨已发布 Issue 检索？

若允许，则：

```
latest Issue  ≠  latest Query Scope

IssueRef     = latest_published   （回答「当前在哪一期」）
TimeWindow   = recent             （回答「时间窗」）
+ 允许跨 Issue retrieval
```

这与已锁定的正交关系一致：**IssueRef ⊥ TimeWindow** —— 不是推翻 Context，而是落实 Retrieval 对这两个维度的用法。

**在此决策落地前：R02/R23/R24 不得计入 FTS/Query 优化范围。**

### ② R14 Index / Data

```
4408–4412
  ↓ 到底有没有进入当前索引？
没进索引 → 不是 Recall 算法问题
```

### ③ FTS / Query（真正的 Recall Phase 1 改动面）

| Case | Missed items |
|------|----------------|
| R11 | 4106 |
| R12 | 4125（仅此；4120/4128 属 Ranking） |
| R13 | 4154–4156 |
| R18 | 4271 |

逐个验证链：

```
原文 token 是否存在？
        ↓
FTS 是否能命中？
        ↓
Query 进入了什么形式？
        ↓
Hybrid 合并是否丢失？
```

### Vector（可选对照 · 不叫「优化」）

baseline = `Embed False` → **不能说 Vector 召回不好，也不能说 Vector 能解决这些 miss。**

可单独对照实验（仍属实验，不接进主线）：

```
同一 Gold
FTS-only  vs  FTS + Vector
→ Recall@5/@10/@20 · 新增召回了谁 · 是否真 relevant
```

### 明确禁止（Phase 1）

- ❌ 因 R12 的 4120@6 / 4128@9 提前上 Rerank / 开 ③  
- ❌ 把 Scope miss 算成 FTS 缺陷  
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
| latest 上搜不到内容 | **② Scope 决策 → 再谈 Recall** |
| 历史内容没被召回 | **② Retrieval Recall** |
| RAG 漏召回 | **② Retrieval Recall** |
| `2026-8-17` date_start/end 脏 | **Data Time Integrity**（数据） |
| 无真实 `event_time` | **诚实默认 unknown**（正确，不伪造） |

---

## 质量层对照

| 质量层 | 状态 |
|--------|------|
| 边界 / Tool / Identity / IssueRef | ✅ 保持 |
| 时间语义 | ✅ Temporal PASS |
| Recall Phase 0 + Failure Review | ✅ PASS |
| Recall Phase 1 | 🟡 READY（Scope → Index → FTS → 重跑 Gold） |
| Ranking | 🔒 LOCKED（待 Recall 达标） |
| Evidence / Answer | 🔒 LOCKED |
| CRM / Data Domain | 🔒 LOCKED |

---

## Code 清单

1. ✅ Temporal PASS  
2. ✅ `eval/retrieval_gold_v1.jsonl`（30）  
3. ✅ `eval/run_recall_baseline.py` → macro R@5/10/20  
4. ✅ **Failure Pattern Review** → `eval/reports/RECALL_FAILURE_REVIEW.md`  
5. 🟡 **Phase 1 READY** — 先 Scope 产品决策，再 R14 核查，再动 FTS/Query  
6. ⑧ 飞书可并行  
