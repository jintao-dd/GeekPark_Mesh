# Agent Quality / v2

> **状态（2026-09-08）**  
> Agent v1 = **GO**  
> **① Temporal = PASS**  
> **② Retrieval Recall Phase 0 = 完成** · **Failure Pattern Review = 完成**（见 `RECALL_FAILURE_REVIEW.md`）  
> **下一刀：** 按 Review 分层决定 Recall Phase 1（Scope / Index / FTS）vs 移交 Ranking；**仍 NO-GO 盲目改 RAG**  
> **原则：先可测量，再优化；先证明问题，再改架构。**

相关：`MESH_AGENT_FULL_ACCEPTANCE.md` · `TEMPORAL_PHASE1.md`  
⑧ 飞书 MVP ∥ 质量线（只接线）。

---

## 门禁链（锁定）

```
v1 GO
 ↓
Temporal Phase 0   Gold 24 + Baseline
 ↓
Temporal Phase 1   24/24 ✅
 ↓
Temporal PASS
 ↓
开放 ② Retrieval Recall   ← 当前
```

---

## v2 主线

```
① Temporal ✅
   时间理解对不对
        ↓
② Recall          ← 当前 Phase 0
   该找的有没有找回来          → Recall@5 / @10 / @20
        ↓
③ Ranking
   找回来以后有没有排对        → MRR / nDCG@10 / Precision@5
        ↓
④ Evidence → ⑤ Answer → ⑥ Data Domain
```

---

## ① Temporal PASS — 已解决 / 不负责

**已解决：** relative time intent · time basis · time filter ·  
`latest_published ≠ recent event` · unknown 不乱说「最近发生」

**不负责（不得混进 Temporal 回改）：**

| 遗留 | 归属 |
|------|------|
| latest 上搜不到内容 | **② Retrieval Recall** |
| 历史内容没被召回 | **② Retrieval Recall** |
| RAG 漏召回 | **② Retrieval Recall** |
| `2026-8-17` date_start/end 脏 | **Data Time Integrity**（数据） |
| 无真实 `event_time` | **诚实默认 unknown**（正确，不伪造） |

---

## ② Retrieval Recall Phase 0（硬门槛 · 交给 Code）

> **只测现状。禁止改 RAG / Chunk / FTS / Vector / Query Rewrite / Rerank / Ranking。**

### 完成条件（不是抬 Recall 分数）

```
Retrieval Gold 建完
    ↓
Baseline runner 跑通
    ↓
只跑当前 Retrieval
    ↓
Recall@5 / @10 / @20
    ↓
每题 relevant / retrieved / missed
    ↓
汇总 failure pattern
    ↓
才允许讨论改检索
```

### Gold（约 30 题）

每题至少：

```json
{
  "id": "R01",
  "query": "...",
  "scope": {
    "issue": "latest_published | explicit:<slug>",
    "time_basis": "event_time | issue_time | unknown",
    "window": "recent | none | explicit"
  },
  "relevant_items": ["4105", "4112"]
}
```

`relevant_items` = 正确答案的 **item id**（必须可核对）。

### Baseline 输出

- 聚合：Recall@5 / @10 / @20  
- 逐题：expected · retrieved · missed  
- failure pattern：人名 / 时间过滤 / Issue scope / FTS / Vector / Hybrid …

报告：`eval/reports/RECALL_BASELINE_latest.json` + `.md`

---

## 质量层对照

| 质量层 | 状态 |
|--------|------|
| 边界 / Tool / Identity / IssueRef | ✅ 保持 |
| 时间语义 | ✅ Temporal PASS |
| Recall | ⬜ Phase 0 |
| Ranking | ⛔ 待 Recall 后 |
| Evidence / Answer | 后段 |
| CRM | ⑥ |

---

## 明确不做（Recall Phase 0）

- ❌ 改 RAG / Chunk / FTS / Vector / Hybrid / Rewrite  
- ❌ 上 Rerank / 改 Ranking  
- ❌ 用 Temporal 补丁假装解决召回  
- ❌ 伪造 event_time  

---

## Code 清单

1. ✅ Temporal PASS  
2. ✅ `eval/retrieval_gold_v1.jsonl`（30）  
3. ✅ `eval/run_recall_baseline.py` → macro R@5/10/20  
4. ✅ **Failure Pattern Review** → `eval/reports/RECALL_FAILURE_REVIEW.md`  
5. ⬜ 按 Review 分层决策 Recall Phase 1（禁止未分类改 RAG）  
6. ⑧ 飞书可并行  
