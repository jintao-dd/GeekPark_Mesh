# Agent Quality / v2

> **状态（2026-09-08）**  
> Agent v1 = **GO**  
> Quality / v2 · Temporal Phase 0 ✅ · **Phase 1 ✅**（Gold 24/24；Intent/Basis/Filter + Hard Rules）  
> 下一刀：**② Retrieval Recall**（仍不把 RAG 当时间补丁）  
> **原则：先可测量，再优化；先证明问题，再改架构。**  

相关：`MESH_AGENT_FULL_ACCEPTANCE.md`（v1 门禁）  
⑧ 飞书 MVP ∥ ① Temporal（只接线，不挡质量线）。

---

## 一句话

| | 解决什么 |
|--|--|
| **Agent v1** | Agent 能不能**安全、正确地工作** |
| **Agent Quality / v2** | 给出的答案是不是**高质量答案** |

---

## v2 主线（锁定）

```
① Temporal
   时间理解对不对
        ↓
② Recall
   该找的有没有找回来          → Recall@5 / @10 / @20
        ↓
③ Ranking
   找回来以后有没有排对        → MRR / nDCG@10 / Precision@5
        ↓
④ Evidence
   每个关键事实有没有依据
        ↓
⑤ Answer
   准确、完整、忠实
        ↓
⑥ Data Domain
   CRM / 其他数据域
```

**硬门：** ① Temporal Gold 验收 PASS 之前，**禁止**进 ②，更禁止「先优化 RAG」。

⑧ 飞书 ∥ ①（并行）。

---

## 质量层对照

| 质量层 | v1 | v2 |
|--------|----|----|
| 边界 / Tool / Identity / IssueRef | ✅ | **保持** |
| 时间语义 | ⚠️ | **① 第一刀** |
| Recall | ⚠️ 25/25=回归 | **② Recall@K** |
| Ranking | ⚠️ 未系统测 | **③ MRR / nDCG / P@5** |
| Evidence / Answer / Abstain | ✅ 有基础 | ④⑤ 提高 |
| CRM 等 | 暂不接 | **⑥ 后半段** |

---

## ① Temporal Grounding

典型失败：「锦涛最近做了什么」→ 人/内容对，旧 Issue 被说成「最近发生」。

**目标：回答里的时间必须有依据。**

### 时间轴（禁止混用）

| 轴 | 含义 |
|----|------|
| Issue Time | 期次窗口 |
| Material Time | 材料进入 / 文档标注时间 |
| Event Time | 事件真实发生时间（优先） |
| Publish Time | 上线时间 |
| CRM Time | CRM 互动时间（⑥） |

### 推理链

```
「最近发生了什么」→ Event Time → 否则 Material Time
                 → 仍无 →「资料未提供发生时间」
```

### 流水线（最小实现形态）

```
Query → Time Intent → Time Basis → Time Filter
      → Retrieval → Evidence → Answer
```

---

## Temporal Phase 0（硬门槛 · 正在执行）

> **现在不要优化 RAG。先建 Gold，再看基线错在哪。**  
> 完成条件：**不是** 24 题全 PASS；而是 Gold 建完 → Baseline 跑通 → 失败分布 → 确认问题边界 → 才最小实现。

### 0.1 Gold：24 题（`eval/temporal_gold_v1.jsonl`）

最少覆盖 6 类：

| 类 | 含义 | 题号 |
|----|------|------|
| A | 最近 / 近期 | T01–T04（含 **「锦涛最近做了什么」**） |
| B | 本周 / 上周 | T05–T08 |
| C | 指定时间范围 | T09–T12 |
| D | Issue 与事件时间冲突 | T13–T16（含脏 date / latest≠recent） |
| E | 没有 Event Time | T17–T20 |
| F | 明确要求按 Issue 查 | T21–T24 |

每题固定字段：`query` · `time_semantics.basis|window` · `issue_scope` · `expected_behavior` · `relevant_evidence`。

### 0.2 Baseline runner（`eval/run_temporal_baseline.py`）

只读当前系统。每题输出：

`query → predicted time basis → predicted window → issue scope → retrieved evidence → final answer → pass/fail → failure_reason`

报告：`eval/reports/TEMPORAL_BASELINE_latest.json` + `.md`

### 0.3 Implement（仅最小 Temporal · **Baseline 之后**）

Hard Rules：

1. `latest_published` **≠** recent event  
2. `unknown` event time **≠** recent  
3. **无时间依据** → 不得用「最近发生 / 本周发生」等确定性措辞  

### 0.4 Temporal v1 验收门

```
Gold → Baseline → 最小实现 → 重跑 Gold
    → 时间理解准确率 · 时间依据正确率 → PASS → 才进 ② Recall
```

---

## ② Retrieval Recall

核心：**找没找到。**  
指标：Recall@5 / @10 / @20。  
Gold：每题 relevant items。  
25/25 回归保留，另立 Retrieval Gold。

## ③ Ranking

核心：**找回来以后排得对不对。**  
指标：MRR · nDCG@10 · Precision@5。  
**是否上 Rerank 由数据决定，不先上。**

## ④ Evidence → ⑤ Answer → ⑥ Data Domain

见前表；④ 用好已有 Evidence/Claim，不新发明 confidence 分；⑥ CRM 等在 ①–⑤ 之后。

---

## Dashboard（最终验收形态）

| 块 | 指标 |
|----|------|
| A. Temporal | 时间理解准确率 · 时间依据正确率 |
| B. Recall | Recall@5 / @10 / @20 |
| C. Ranking | MRR · nDCG@10 · Precision@5 |
| D. Evidence | correctness · coverage · unsupported rate |
| E. Answer | Accuracy · Completeness · Faithfulness · Citation |
| F. Safety | Abstention · Published-only · Permission（保持 v1） |

---

## 明确不做

- ❌ Temporal PASS 前优化 RAG / 上 Rerank  
- ❌ 无 Gold 先改架构  
- ❌ 回改 v1 边界 / Contract  
- ❌ Planner / ReAct /「更像 Agent」  
- ❌ CRM 抢跑  

---

## Code 立刻执行清单（Phase 0）

1. ✅ `eval/temporal_gold_v1.jsonl`（24 题 · A–F）  
2. ✅ `eval/run_temporal_baseline.py`（只测现状）  
3. ✅ tmesh baseline → `TEMPORAL_BASELINE_latest.json` / `.md`（22/24 启发式；真失败 T06/T14）  
4. ⬜ **审失败分布后** → 最小 Temporal 实现（仍不改 RAG）  
5. ⑧ 飞书接线可并行  
