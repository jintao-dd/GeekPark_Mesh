# Agent Quality v2.1（2026-09-08）

> Overnight 收口后首轮实施。Vector = **OFF**。生产 ranking **未合并**。

## 状态板

| 层 | 结果 | 决策 |
|----|------|------|
| Temporal | **24/24**（T19 evaluator CAVEAT 对齐后全量重跑） | 产品不动 |
| Retrieval Recall | **83 / 90 / 94**（冻结） | 不变 |
| Ranking v1.1 | MRR **0.855** · nDCG@10 **0.845** · P@5 **0.517** | 实验保留；**不合并生产** |
| Evidence v2 | pass **80%**（含对抗题） | 测量加强有效；不作对外宣称 |
| Answer v2 | hard pass **71%** · acc **0.71** | 假通过已拆掉 |
| Vector | OFF | — |

### 指标口径（不变）

- **Retrieval Recall** → R@5 / R@10 / R@20  
- **Ranking-adjusted** → MRR / nDCG@10 / P@5（可选 post-ranking R@k）  
- Evidence / Answer → 最终说得对不对（硬门槛）

---

## ① Temporal T19

- 归因：**evaluator 假阴性**（否定句命中「最近发生」子串）
- 修复：`unknown_basis_but_claimed_recent` 对齐 CAVEAT
- 全量重跑：**24/24 PASS**
- **Temporal 产品代码未改**

---

## ② Ranking v1.1

相对 Ranking baseline（生产序）：

| | baseline | v1.1 | Δ |
|--|----------|------|---|
| MRR | 0.8071 | **0.8554** | **+0.0483** |
| nDCG@10 | 0.8250 | **0.8446** | **+0.0196** |
| P@5 | 0.5106 | **0.5172** | **+0.0066** |
| post-rank R@20 | 0.9433 | 0.9433 | 0（召回集合未改） |

### 焦点误杀

| 题 | v1（旧） | v1.1 | 相对 baseline |
|----|----------|------|----------------|
| **R06** | ok→top5_miss | **ok** | 误杀已消 |
| **R13** | 更差 | 与 baseline 同 | 无新增误杀 |
| **R18** | 略差 | nDCG **−0.011** | 轻微倒退，可接受观察 |

手段：标题增益封顶 + **query term 覆盖率**衰减 + **原始 FTS 位次阻尼** + 同族 bucket 阻尼；team/issue/time 仅 soft。

**决策：KEEP current production ranking；ranking_v1_1 为当前最佳实验版。**  
合并门槛未完全满足（R18 微负、R12 仍有 Top5 抖动）→ 下一刀再谈 production。

---

## ③ Answer Gold v2（硬检查）

- 去掉 mean≥0.75 假通过；要求非空、accuracy/faithfulness/abstention 硬门槛  
- n=17（含 A16/A17 unsupported claim）  
- **pass_rate 0.71**（对外不吹这个数；它是在暴露问题）  
- 失败信号：A03/A04/A08（must_mention 未进摘要）、A07/A17（abstention）

---

## ④ Evidence Gold v2（对抗）

- 增加 E13–E15：「有 evidence 但不能支持该 claim」  
- pass_rate **0.80** · unsupported_rate **0.20**（对抗题开始拉低虚高 100%）  
- 证明：evidence exists ≠ evidence supports answer

---

## ⑤ Unified

入口：`eval/run_quality_v2_1.py --reuse-env-db`  
汇总：`eval/reports/QUALITY_V2_1_SUMMARY.json`

---

## 下一步

1. Ranking：稳住 R18 / 改善 R12 Top5，再评估是否合并  
2. Answer：摘要路径补 must_mention；abstention 题接真实拒绝话术  
3. Evidence：对抗题失败 case 逐条看答案是否泄漏 claim  
4. 仍不做：Vector 主线、改 Gold 刷分、Planner/ReAct/CRM
