# Agent Quality v2.4 · Fixed-model LLM-on Baseline

> **只测不修。** claim_support v2.3e / Ranking v1.4 / Ontology / Contract：**零改动。**  
> 目标：摸清真实 LLM 路径 vs deterministic，而不是刷 pass_rate。

## Baseline lock

| 字段 | 值 |
|------|-----|
| `MESH_AGENT_USE_LLM` | **1**（真实成文） |
| `model_version` | `anthropic/claude-4.8-opus`（本轮**确实调用**） |
| Ranking | v1.4 frozen |
| Vector | OFF |
| claim_support | v2.3e frozen |
| Prompt | ask_engine + temporal hard rules + claim_support → `llm.answer_question` |

Canonical Answer 中 `llm_used`：**11 / 21**（其余被 claim_support abstain / temporal early / 空命中短路）。

---

## 结果总表（vs deterministic v2.3e）

| Layer | Deterministic | LLM-on | Delta |
|-------|---------------|--------|-------|
| **Temporal** | 0.875（T03/T19/T20） | **0.958**（仅 **T19**） | LLM 清掉 T03、T20；T19 仍在 |
| **Answer canonical** | 21/21 = 1.0 | **21/21 = 1.0** | 无回归 |
| **Evidence canonical** | 20/20 = 1.0 | **20/20 = 1.0** | 无回归 |
| **Unseen Answer** | 0/7 = 0.0 | **2/7 ≈ 0.286**（A24、A26） | LLM 部分改善 Answer 拒答 |
| **Unseen Evidence** | 1/8 = 0.125 | **1/8 = 0.125**（仅 E28） | Evidence **label** 未改善 |

JSON：`eval/reports/baselines/QUALITY_V2_4_LLM_ON.json`

---

## 三问回答

### Q1 · LLM path 比 deterministic 多解决 / 多引入了什么？

**多解决（有限）：**
- Temporal：T03、T20 在本轮 LLM-on 下通过（相对 v2.3e 报告的 0.875）
- Unseen Answer：A24「量产车型采用」、A26「批量交付」硬门槛 pass（拒答措辞过门）

**未解决 / 仍暴露：**
- Canonical 已绿，LLM **没有**破坏已知 A21/E16/E18 冻结集
- Unseen Evidence 仍全挂在 `expect=insufficient` vs `obs=supported`（**label**）
- 多数 Unseen Answer 仍 fail（A22/A23/A25/A27/A28）

**多引入：**
- 本轮未见 canonical 新 hard fail
- Unseen 上 LLM 成文更「像分析」，但仍常把相关证据讲成可讨论对象；部分题靠「未提供/无法确认」过 abstain 门，**不等于** claim_support 已识别强 claim

### Q2 · Temporal 三缺口是不是路径差异？

| 题 | v2.3e 报告 | v2.4 LLM-on |
|----|------------|-------------|
| T03 | fail | **pass** |
| T19 | fail | **fail**（`forbid_recent_event_claim` 类） |
| T20 | fail | **pass** |

**判断：** 先前 21/24 很大程度是 **成文/路径波动**，不是独立于 LLM 的第三条稳定缺陷面。  
T19 在固定 LLM-on 下仍失败 → **值得单独记为真实 Temporal 残留**，但本轮不修。

### Q3 · Unseen claim-strength：规则泛化不足还是模型判断不足？

关键证据：

1. **claim_support 对 Unseen 仍全部 `claim_strength=weak` → `support=supported`**  
   → deterministic strength 门**没有**泛化到「装车/规模化生产/批量交付」等 paraphrase。

2. **Evidence Gold**：`abstention_ok=1.0` 但 `pass=false`（support label 不对）  
   → 成文层常已 caveat/拒答，**Evidence 语义标签仍 false-support**。  
   → 再次验证：**Evidence label ≠ Answer safety**。

3. **LLM 仅抬升 Answer unseen 0→0.286**，Evidence unseen **不变**  
   → 模型对部分问法能「说不敢确认」，但**不能替代** claim_support 的 strength / entailment 标签。

**结论倾向（供下一刀决策，本轮不实施）：**
- 纯加关键词黑名单：**不推荐**（已有诊断）
- 纯靠 LLM 当 Evidence label：**不够**（label 仍 supported）
- 更合理长期形态仍是你写的：Claim normalization → strength class → evidence entailment → support；实现上偏 **C 组合**（规则管 class，LLM 可辅助 entailment），但要另开实验，不在本 baseline 里改代码。

---

## 明确未做

- ❌ 未改 `claim_support.py`
- ❌ 未合入 unseen 到主 Gold
- ❌ 未做 Model A/B / router
- ❌ 未扩建 Ontology
- ❌ 未动 Ranking / Recall / Vector / Wiki / ES

---

## 下一阶段建议（等你点头）

1. 把 v2.4 本报告定为 **LLM-on baseline 1.0**  
2. 再开分析刀：在 **不改规则** 前提下，抽样 A22/E21 看 LLM 是否「嘴上 caveat、标签仍 supported」  
3. 再决策 A / B / C  
4. 然后才 Model A/B（单变量换模型）

产物：
- Runner：`eval/run_quality_v2_4_llm_on.py`
- `eval/reports/baselines/QUALITY_V2_4_LLM_ON.json`
- Unseen 明细：`eval/reports/UNSEEN_V24_LLM_ON.json`
