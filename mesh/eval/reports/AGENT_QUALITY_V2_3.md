# Agent Quality v2.3（2026-09-09）

> 边界已锁定：Ontology **从现有对象反推**；不做 KG / Wiki / 新 Contract。  
> 本轮主攻 Ranking：**R16 回退消除** + **R12 稳定**；4125→Top5 仍未闭环。

## 执行边界（重申）

| 现在做 | 现在不做 |
|--------|----------|
| Ranking 4125 / R16 | Neo4j / Graph RAG / 新 Retrieval |
| Answer hard fail（下轮） | 新 Tool / Planner / ReAct |
| Evidence claim→support | LLM Wiki |
| 轻量 Ontology 反推稿 | 因 Ontology 改 Recall/Vector/Contract |

文档：`docs/MESH_LIGHTWEIGHT_ONTOLOGY.md`

---

## Ranking v1.3 vs production baseline

| 指标 | baseline | v1.3 | Δ |
|------|----------|------|---|
| MRR | 0.8071 | **0.8310** | **+0.0239** |
| nDCG@10 | 0.8250 | **0.8389** | **+0.0139** |
| P@5 | 0.5106 | **0.5106** | **0** |
| post-rank R@5/10/20 | 0.826/0.896/0.943 | **同左** | **0** |

### 焦点

| 题 | 结果 |
|----|------|
| **R06** | ✅ 保持 ok |
| **R18** | ✅ nDCG Δ 0（不回退） |
| **R16** | ✅ 相对 baseline nDCG **+0.017**（消掉 v1.2 的 −0.012）；4131 回 Top5 |
| **R12** | 🟡 4120 留在 Top5；nDCG **+0.051**；**4125 仍 @7 / 4128 @14** |
| 全量 Gold 误杀 | ✅ `worse=[]` |

**决策：KEEP current production ranking；`ranking_v1_3` 实验保留。**  
合并仍卡在：**4125 未进 Top5**（主目标未满）。R16 挡合并的问题已解除。

手段：v1.2 底 + Top5 加固 + 第 6 名轻护 + 仅对 orig 7–9 高 phrase 轻推（避免再伤 R06）。

---

## Ontology

- 已落盘反推稿（非系统）
- 原则：先整理 item_facts / Relation / EvidenceRef / Entity / team / event_time，**不**先画理想模型

---

## 下一刀

1. Ranking：专治 **4125→Top5**（在 worse=[] 前提下），再谈合并  
2. Answer：逐题清剩余 hard fail  
3. Evidence Gold：用轻量 schema 描述 claim/support  
4. 仍不碰 Recall / Vector / Wiki
