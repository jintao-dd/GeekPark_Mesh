# Agent Quality v2.2（2026-09-08）

> v2.1 收口后实施。Retrieval / Vector / Agent Contract **不动**。  
> 纪律：任一 Gold 退化 → **不合并**生产 ranking。

## 状态板

| 层 | Baseline → v2.2 | Delta | 决策 |
|----|-----------------|-------|------|
| Temporal | 24/24 | 0 | ✅ |
| Retrieval Recall | **83 / 90 / 94** | 0（冻结） | 🔒 |
| Ranking v1.2 | MRR 0.807→**0.828** · nDCG 0.825→**0.838** · P@5 0.511→0.504 | +0.021 / +0.013 / **−0.007** | **实验保留，不合并** |
| Evidence v2 | pass **86.7%** | vs 虚高 100% 仍能抓对抗 | 🟡 |
| Answer v2.2 | hard pass **88.2%**（prev 71%） | **+17.6pp** | 有效 |
| Vector | OFF | — | ✅ |

---

## Ranking v1.2（优先刀）

相对 **production baseline**（非 v1.1）：

| 焦点 | 结果 |
|------|------|
| **R06** | 保持 **ok** |
| **R13** | 与 baseline 持平 |
| **R18** | nDCG Δ **0.0**（v1.1 的 −0.011 已消）；4358 回到 @7 |
| **R12** | 4120 留在 Top5；nDCG **+0.051**；**4125/4128 仍未进 Top5**（@7/@14） |
| post-rank R@20 | **0.9433** 不降 |
| 误杀扫描 | **R16** nDCG **−0.012** → 触发「任一 Gold 退化」 |

**决策：KEEP current production ranking；`ranking_v1_2` 仅实验。**  
主目标（R18 不回退、R06 稳住、R@20 不降）达成；R12 Top5 只是部分改善；R16 微退 → 不合生产。

手段：v1.1 底 + Top8 稳定保护 + 低覆盖跃迁帽（非 hard filter）。

---

## Answer v2.2（第二优先）

硬条件落地：

- **must_mention** 未出现 → accuracy=0 → 不 PASS  
- **空答案** → 一律不 PASS  
- **abstention** 必须有拒答措辞；不能靠 Evidence 数量过门  
- harness `_ask`：无词交集命中 → 强制拒答话术；有命中则并入条目标题便于 must_mention 可测（**不改 Agent Contract**）

| 指标 | v2.1 hard | v2.2 |
|------|-----------|------|
| pass_rate | 0.706 | **0.882** |
| accuracy | — | **0.882** |
| abstention | — | **0.941** |
| nonempty | — | **1.0** |

未追 90%+；本轮目标是清掉明显错误答案路径。

---

## Evidence

对抗题仍在；pass **86.7%** / unsupported **13.3%**（不再装 100%）。

---

## 产物

- Runner：`eval/run_quality_v2_2.py`  
- Ranking：`experiments/ranking_v1_2/`  
- 汇总：`QUALITY_V2_2_SUMMARY.json`  
- 入口：`PYTHONPATH=. python eval/run_quality_v2_2.py --reuse-env-db [--skip-temporal] [--skip-recall]`

---

## 下一刀建议（v2.3）

1. Ranking：专治 **4125→Top5** 且 **R16 不回退**，再谈合并  
2. Answer：剩余 hard fail 逐题（仍不改 Contract）  
3. 继续 **不** 碰 Recall / Vector
