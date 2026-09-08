# Agent Quality v2 · Overnight Batch（2026-09-08）

> 目标：把 Temporal → Retrieval → Ranking → Evidence → Answer 建成**可测 + 可优化**闭环。  
> 纪律：不覆盖 frozen baseline；实验不好则保留；不改 Agent v1 Contract / Permission / Published-only。

环境：`tmesh` · Embed baseline=`False` · Gold Recall/Ranking n=30 · Evidence n=12 · Answer n=15

---

## 一页状态板（明早决策用）

| 层 | Baseline | Current / Best | Delta | 决策 |
|----|----------|----------------|-------|------|
| **Temporal** | 24/24（历史 Phase1） | 本晚 runner **23/24**（T19） | 见下 | **产品未推翻**；T19 优先核查真回归 vs evaluator |
| **Retrieval Recall** | **83 / 90 / 94** | 83 / 90 / 94 | 0 | **冻结**（候选集合指标） |
| **Ranking** | MRR 0.807 · nDCG@10 0.825 · P@5 0.511 | **v1** 0.863 / 0.862 / 0.524 | +0.056 / +0.037 / +0.013 | **KEEP current production ranking；ranking_v1 仅实验** |
| **Evidence** | cov/corr/cite=1.0 · pass=100% | = baseline | n/a | **结构/引用可测**；≠ claim 已 substantiated |
| **Answer** | pass 93% · acc 0.73 · faith 0.91 | = baseline | n/a | **93% 不对外部讲**；评分偏松，acc 才是信号 |
| **Vector A/B** | FTS 83/90/94 | FTS+Vec **76/83/88** | **−6.7pp** | **Vector experiment = OFF**（当前融合方案负收益 ≠ 向量永远无用） |

**指标口径（锁定）：**

| Dashboard 栏 | 含义 | 主指标 |
|--------------|------|--------|
| **Retrieval Recall** | 召回候选集合 | R@5 / R@10 / R@20 |
| **Ranking-adjusted Top-K** | 同一候选上改序后的 Top-K | MRR / nDCG@10 / P@5；（可选）post-ranking R@5/@10 |

Ranking 实验里 R@5 从 82.6%→85.4% 应称 **post-ranking Top-K Recall**，证明 R12 类问题已进入 Ranking，**不要**与 Retrieval Recall 混报。

**当前生产主线：** FTS-only + 现有 `retriever.rerank_hits`。  
**实验版本（未上线）：** `experiments/ranking_v1`。

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

### E0 · Temporal（只读）→ T19 初判

- 跑：`run_temporal_baseline.py` → n=24 · runner **23/24** · fail **T19** `unknown_basis_but_claimed_recent`
- **答案实际：** 明确写「不能推断为昨天/最近发生」「资料未提供 event_time」——产品 hard rule 方向正确
- **失败原因：** evaluator 对 `最近发生` 子串命中（否定句里的「昨天/最近发生」），且 `unknown_basis_but_claimed_recent` **未做 CAVEAT 豁免**（与 `forbid_recent_event_claim` 不一致）
- **归因：评测启发式误报（false positive），不是 Temporal 架构回归**
- **动作：** 修评测规则对齐 CAVEAT；不改产品 Temporal

### E1 · Ranking Phase 0（baseline）

| 指标 | 值 |
|------|-----|
| MRR | **0.8071** |
| nDCG@10 | **0.8250** |
| Precision@5 | **0.5106** |
| post-ranking R@5/@10/@20（同候选、现网序） | 0.826 / 0.896 / 0.943 |

重点案例 **R12**（baseline ranks）：4116@3 · 4120@5 · 4125@7 · 4128@14 → `top5_miss:4125,4128`

### E2 · Ranking Phase 1（`apply_ranking_v1` 仅评测路径）

信号：标题/专名命中、item_entity_facts / item_facts 来源小幅加权（**不改召回集合**）。

| 指标 | baseline | v1 | Δ |
|------|----------|----|---|
| MRR | 0.8071 | **0.8632** | **+0.0561** |
| nDCG@10 | 0.8250 | **0.8615** | **+0.0365** |
| P@5 | 0.5106 | **0.5239** | **+0.0133** |
| post-ranking R@5（非 Retrieval） | 0.8261 | 0.8544 | +0.0283 |

**提升题（示例）：** R11（4106 23→4）、R12（MRR↑，4116→@1；4120 仍@5；4128 仍差）、R16、R21。

**误杀（必须报告）：**

| 题 | ΔMRR | ΔnDCG@10 | 现象 |
|----|------|----------|------|
| **R06** | 0 | **−0.082** | 原 ok → `top5_miss:4150,4151`（4150 4→6，4151 5→7） |
| **R13** | **−0.25** | −0.094 | 4156 2→4；4154/4155 仍远 |
| **R18** | −0.032 | −0.020 | 4358 7→9 |

**决策：KEEP current production ranking；ranking_v1 保留为实验版本（未上线）。**  
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
| Temporal 本晚重跑 | **T19 = evaluator 假阴性** · 产品答案合规 · 已修评测 CAVEAT 对齐 |

**Best 配置（生产）：** Temporal 现网 + Recall FTS-only Phase1 + 现网 rerank。  
**Best 配置（实验架）：** Ranking profile=`v1` 仅用于对比，直到误杀清零。

---

## 新 failure patterns

1. **Ranking 误杀：** 标题加权过猛 → 同族多 item（R06）挤出 Top5  
2. **R12 未闭环：** 4120 已在 Top5 边缘；4125/4128 仍非 Top5（Ranking 仍开放）  
3. **Vector 稀释：** 向量 lane 拉低 Recall、抬高 top5 noise  
4. **Temporal T19：** runner 误报（否定句命中「最近发生」）；产品未违 hard rule  
5. **Answer 空答 + 宽松 pass：** 测量框架在，但对「可读答案质量」仍偏乐观；**93% pass 不对外部讲**  

---

## 下一步 · Agent Quality v2.1（数据驱动，不重开大规划）

```
① T19          确认假阴性 → 修 evaluator（本轮已对齐 CAVEAT）· 不改 Temporal 产品
② Ranking v1.1 消 R06/R13/R18；标题增益非霸权；team/issue/time 仅 soft；先验证同族挤压
③ Answer Gold  收紧评分；去掉「证据数量=正确」假通过；盯 A07 / A03/A04/A08
④ Evidence Gold 加「有 evidence 但不支持 claim」对抗题
⑤ Unified rerun
Vector         OFF（当前融合方案）
```

**不做：** 覆盖 baseline · 改 Gold 刷分 · 合并 ranking_v1 进生产 · 接 Vector 主线 · Planner/ReAct/CRM

---

## 产物索引

- Runner：`eval/run_quality_v2.py` · `run_ranking_baseline.py` · `run_evidence_baseline.py` · `run_answer_baseline.py` · `run_vector_ab.py` · `quality_metrics.py`  
- Gold：`ranking_gold_v1.jsonl` · `evidence_gold_v1.jsonl` · `answer_gold_v1.jsonl`  
- 汇总：`QUALITY_V2_OVERNIGHT_SUMMARY.json`
