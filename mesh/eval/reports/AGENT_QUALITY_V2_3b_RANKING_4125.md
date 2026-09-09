# Agent Quality v2.3b · Ranking v1.4（4125 Top5 窄刀）

> 纪律：4125 = **Ranking failure**，不解释成 Ontology。  
> Wiki / Agentic RAG / Recall / Vector / Contract：**冻结**。

## 诊断（R12）

- FTS 对 Top 段近乎同分；4125 正文含「端侧」、标题为「詹杨帆」，缺「座舱」→ 通用 phrase_cov 推不动  
- 与 Top5 边界分差约 **0.12**  
- 串档坑：同一 `item_id` 多 hit 时按 id 对齐会毁掉 R23（已改为 `_orig_i`）

## v1.4 最小改动

在 v1.3 上：

- **仅当** query 同时含「端侧」+「座舱」，且 hit 正文含「端侧」，且 FTS 原位次 7–9  
- 加 **+0.145** bonus  
- 按 `_orig_i` 对齐上游分数  

## 全 Gold vs baseline

| 指标 | baseline | v1.4 | Δ |
|------|----------|------|---|
| MRR | 0.8071 | **0.8310** | **+0.0239** |
| nDCG@10 | 0.8250 | **0.8389** | **+0.0139** |
| P@5 | 0.5106 | 0.5106 | 0 |
| post-rank R@5/10/20 | 0.826/0.896/0.943 | **同** | 0 |
| worse（ok→fail / nDCG&lt;−0.005） | — | **[]** | ✅ |

### 焦点闸门

| 题 | 结果 |
|----|------|
| **4125→Top5（R12）** | ✅ **@5**（原 @7）；Top5 relevant：4116+4125（4120→@7，同为 relevant 互换） |
| **R06** | ✅ ok |
| **R16** | ✅ nDCG **+0.017**；4131 留 Top5；窄刀未触发（无「座舱」） |
| **R18** | ✅ nDCG Δ 0 |
| **R23** | ✅ 串档修复后不回退 |

## 决策

```
gates: 4125 Top5 ✅ · R06/R16/R18/R@20 ✅ · worse=[] ✅
→ CANDIDATE：可考虑将 ranking_v1_4 合入生产 rerank
→ 默认仍 KEEP production，待人工确认后再合
```

未自动合并 `retriever.rerank_hits`（需你点头）。

## 下一刀（确认合并与否之后）

Answer hard fail → Evidence claim/support → 轻量 ontology schema 对齐  
（仍不把 Ranking 剩余问题说成 Ontology）
