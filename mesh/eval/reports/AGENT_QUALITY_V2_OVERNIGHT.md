# Agent Quality v2 · Overnight Batch（2026-09-08）

> 目标：把 Temporal → Retrieval → Ranking → Evidence → Answer 建成**可测 + 可优化**闭环。  
> 纪律：不覆盖 frozen baseline；实验不好则保留；不改 Agent v1 Contract / Permission / Published-only。

环境：`tmesh` · Embed baseline=`False` · Gold Recall/Ranking n=30 · Evidence n=12 · Answer n=15

---

## 一页状态板（明早决策用）

| 层 | Baseline | Current / Best | Delta | 决策 |
|----|----------|----------------|-------|------|
| **Temporal** | 24/24（历史 Phase1） | **23/24**（T19） | −1 题回归 | **保留** Temporal 实现；T19 记 failure，不今晚改架构 |
| **Recall** | **83 / 90 / 94** | 83 / 90 / 94 | 0 | **冻结** `baselines/RECALL_BASELINE_v1.json` |
| **Ranking** | MRR 0.807 · nDCG@10 0.825 · P@5 0.511 | **v1** 0.863 / 0.862 / 0.524 | +0.056 / +0.037 / +0.013 | **候选有效，但今晚不合并生产**（见误杀） |
| **Evidence** | cov/corr/cite = 1.0 · unsupported=0 · pass=100% | = baseline（首 freeze） | n/a | **冻结** Evidence baseline |
| **Answer** | pass 93% · acc 0.73 · faith 0.91 · cite 1.0 · abstain 0.93 | = baseline | n/a | **冻结** Answer baseline；acc 是下一步 |
| **Vector A/B** | FTS 83/90/94 | FTS+Vec **76/83/88** | **−6.7 / −6.7 / −6.7** | **不接主线**；noise↑ relevant 新增=0 |

**当前生产主线：** FTS-only + 现有 `retriever.rerank_hits`（Ranking baseline 路径）。  
**当前实验最佳（未上线）：** `experiments/ranking_v1`（宏指标胜，3 题误杀）。

---

## Frozen baselines（禁止覆盖）

| 名 | 路径 |
|----|------|
| RECALL_BASELINE | `eval/reports/baselines/RECALL_BASELINE_v1.json`（亦有 `RECALL_BASELINE_latest.json` 镜像） |
| RANKING_BASELINE | `eval/reports/baselines/RANKING_BASELINE_v1.json` + `RANKING_BASELINE_latest.json` |
| EVIDENCE_BASELINE | `eval/reports/EVIDENCE_BASELINE_latest.json`（首跑即 freeze） |
| ANSWER_BASELINE | `eval/reports/ANSWER_BASELINE_latest.json` |
| QUALITY_V2 | `eval/reports/QUALITY_V2_OVERNIGHT_SUMMARY.json` + 本报告 |
| Vector | `eval/reports/experiments/vector_ab/VECTOR_AB_DELTA.json` |

统一入口：`PYTHONPATH=. python eval/run_quality_v2.py --reuse-env-db [--skip-vector]`

---

## 实验清单与 Delta

### E0 · Temporal regression（只读）

- 跑：`run_temporal_baseline.py`
- 结果：n=24 · **n_pass=23** · fail **T19** `unknown_basis_but_claimed_recent`
- Delta vs 历史 24/24：−1
- **动作：** 不回改 Temporal 架构；记入 failure pattern；下一轮单独修 T19 措辞/basis 判定

### E1 · Ranking Phase 0（baseline）

| 指标 | 值 |
|------|-----|
| MRR | **0.8071** |
| nDCG@10 | **0.8250** |
| Precision@5 | **0.5106** |
| R@5/10/20 | 0.826 / 0.896 / 0.943（与 Recall 一致） |

重点案例 **R12**（baseline ranks）：4116@3 · 4120@5 · 4125@7 · 4128@14 → `top5_miss:4125,4128`

### E2 · Ranking Phase 1（`apply_ranking_v1` 仅评测路径）

信号：标题/专名命中、item_entity_facts / item_facts 来源小幅加权（**不改召回集合**）。

| 指标 | baseline | v1 | Δ |
|------|----------|----|---|
| MRR | 0.8071 | **0.8632** | **+0.0561** |
| nDCG@10 | 0.8250 | **0.8615** | **+0.0365** |
| P@5 | 0.5106 | **0.5239** | **+0.0133** |
| R@5 | 0.8261 | 0.8544 | +0.0283 |

**提升题（示例）：** R11（4106 23→4）、R12（MRR↑，4116→@1；4120 仍@5；4128 仍差）、R16、R21。

**误杀（必须报告）：**

| 题 | ΔMRR | ΔnDCG@10 | 现象 |
|----|------|----------|------|
| **R06** | 0 | **−0.082** | 原 ok → `top5_miss:4150,4151`（4150 4→6，4151 5→7） |
| **R13** | **−0.25** | −0.094 | 4156 2→4；4154/4155 仍远 |
| **R18** | −0.032 | −0.020 | 4358 7→9 |

**决策：KEEP 生产 ranking；v1 保留为 experiment-best。**  
理由：宏指标正向，但 R06 从合格变不合格 = 不可接受的误杀；不符合「失败自动保留 baseline」。  
**不上独立 LLM Rerank**（本轮规则分已够说明方向；先消误杀再谈 rerank）。

### E3 · Retrieval · FTS-only vs FTS+Vector（A/B，不切主线）

| | R@5 | R@10 | R@20 |
|--|-----|------|------|
| FTS-only | 0.8261 | 0.8961 | 0.9433 |
| FTS+Vector | 0.7594 | 0.8294 | 0.8767 |
| **Δ** | **−0.0667** | **−0.0667** | **−0.0666** |

- **新增 relevant：** 0  
- **noise：** 多题 top5 noise↑（R01/R03/R07/R08/R09/R17/R24/R26/R27/R29 等）  
- **决策：不接 Vector 主线。**

### E4 · Evidence baseline（首 freeze）

复用 EvidenceRef / ClaimBinding / Citation；**无 truth score**。

| coverage | correctness | unsupported | citation | pass |
|----------|-------------|-------------|----------|------|
| 1.0 | 1.0 | 0.0 | 1.0 | 100%（n=12） |

说明：当前 harness 走 `ask_engine.prepare` 摘要绑定，偏「结构/引用可测」；深度 claim 正确性下一轮加难例。

### E5 · Answer baseline（n=15，首 freeze）

| accuracy | completeness | faithfulness | citation | abstention | pass_rate |
|----------|--------------|--------------|----------|------------|-----------|
| 0.7333 | 0.9067 | 0.9133 | 1.0 | 0.9333 | 0.9333 |

- **硬失败：** A07（abstain/faithfulness 全挂）  
- **accuracy=0 但仍整体 pass：** A03 / A04 / A08（启发式 mean≥0.75；说明 Answer Gold 评分还需收紧）  
- 若干题 `answer` 文本为空、靠证据计数过门 → **下一轮必须接真实 Answer 路径或固定摘要模板再评**

---

## 有效 / 回退 / 最佳

| 改动 | 结果 |
|------|------|
| Recall Phase1 FTS（已上线） | **有效** · 冻结 83/90/94 |
| Ranking v1 规则加权（评测） | **宏有效 / 有误杀** · **不合并** |
| FTS+Vector | **回退** · 保留 FTS-only |
| Evidence/Answer runners | **有效（建立测量）** · 无生产逻辑变更 |
| Temporal 本晚重跑 | **发现 T19 回归** · 不改代码 |

**Best 配置（生产）：** Temporal 现网 + Recall FTS-only Phase1 + 现网 rerank。  
**Best 配置（实验架）：** Ranking profile=`v1` 仅用于对比，直到误杀清零。

---

## 新 failure patterns

1. **Ranking 误杀：** 标题加权过猛 → 同族多 item（R06）挤出 Top5  
2. **R12 未闭环：** 4120 已在 Top5 边缘；4125/4128 仍非 Top5（Ranking 仍开放）  
3. **Vector 稀释：** 向量 lane 拉低 Recall、抬高 top5 noise  
4. **Temporal T19：** `unknown_basis_but_claimed_recent`  
5. **Answer 空答 + 宽松 pass：** 测量框架在，但对「可读答案质量」仍偏乐观  

---

## 下一步建议（按 ROI）

1. **Ranking v1.1：** 限制同 query 下多 hit 的标题加成；加 team/issue/time match；专测 R06 不回归后再谈合并 `rerank_hits`  
2. **Answer：** 收紧评分；固定非空摘要生成；修 A07 abstention；可选 5 题接真实 LLM（仍不改 Contract）  
3. **Temporal T19** 单点修复 + 回归 24/24  
4. **Vector：** 本轮关闭；仅当 hybrid 能证明 R@k↑ 且 noise 可控时再开实验档  
5. **不做：** Planner / ReAct / CRM / 改 Gold 刷分 / 覆盖 baseline  

---

## 产物索引

- Runner：`eval/run_quality_v2.py` · `run_ranking_baseline.py` · `run_evidence_baseline.py` · `run_answer_baseline.py` · `run_vector_ab.py` · `quality_metrics.py`  
- Gold：`ranking_gold_v1.jsonl` · `evidence_gold_v1.jsonl` · `answer_gold_v1.jsonl`  
- 汇总：`QUALITY_V2_OVERNIGHT_SUMMARY.json`
