# Agent Quality v2.3b → v2.3c · Ranking 合入 + Claim→Support

> 纪律：分层质量；不改 Agent Contract；不放宽 eval；Recall/Vector/Wiki/ES 冻结。

## 一、Ranking v1.4 → 生产（冻结）

| 项 | 结果 |
|----|------|
| 合入点 | `app/ranking_quality.py` + `retriever.rerank_hits`（默认开启） |
| 开关 | `MESH_RANKING_QUALITY=0` 仅评测 A/B |
| 决策 | **MERGED + FROZEN** — 除非新 Gold 暴露退化，不再做 Ranking 优化 |

回归闸门（合入后 `profile=baseline` 即生产路径）：4125→Top5、R06、R16、R18、R@20、worse=[]（见本地/tmesh smoke）。

## 二、Answer hard fail（逐题归因）

| ID | 原现象 | 归因 | 修复 |
|----|--------|------|------|
| **A12** | must_mention「不等于」失败；harness 走空拒答 | **H** harness 未走 Temporal early | `_ask` 对齐 `maybe_direct_answer` |
| **A17** | 期望 abstain，却用面壁相关条目成文 | **C**（兼 **G**：evidence 不支持 claim） | `claim_support` 确定性主张 → abstain |

未放宽 Gold；未改 Contract。

## 三、Evidence claim/support

- `support ∈ {supported, insufficient, contradicted}`
- Gold v2 对抗题标注 `expect_support=insufficient` + claim_schema 字段
- harness / adapter 共用 `app/agent/claim_support.py`
- Published-only · Temporal hard rules **不变**

## 四、轻量 Ontology

仅文档 + Gold/判定对齐：`docs/MESH_LIGHTWEIGHT_ONTOLOGY.md`  
禁止：Neo4j / KG / Graph RAG / 新 Tool / 新 Retrieval / Wiki / 改 Recall·Vector·Contract。

## 五、冻结板

| 层 | 状态 |
|----|------|
| Retrieval Recall | 🔒 |
| Vector | 🔒 OFF |
| Ranking | 🔒 **v1.4 生产** |
| Agent Contract | 🔒 |
| Wiki / ES | 后置 |

下一投入点：**Claim → Evidence → Support → Answer** 链继续收 hard fail / 对抗题，而不是 RAG 架构。
