# Retrieval Recall · Failure Pattern Review

> **状态（2026-09-08）**  
> Recall Phase 0 = **PASS** · Failure Review = **PASS**  
> Phase 1 Scope = **✅ DONE**（IssueRef ⊥ TimeWindow · recent 可跨已发布）  
> R02 / R23 / R24 = **Scope 产品验证样本**（已从 Recall 缺陷名单移除）  
> **NEXT：** R14 Index 核查 → FTS/Query（7 miss）→ 重跑 Gold  
> **NO-GO：** 因 Ranking 证据上 Rerank / 未分层改 RAG  

基线：`RECALL_BASELINE_latest.json`（n=30，Embed=False）  
宏观 Recall@5/@10/@20 = 74% / 79% / 79% —— Phase 0 诊断快照；Scope 落地后需对 R02/R23/R24 重测。

对照题（同内容、不同 scope）—— **变更前** 快照：

| 对照 | latest（变更前） | explicit:2026-8-17 |
|------|------------------|---------------------|
| 锦涛 | R02 miss | R01 / R25 **ok** |
| 破壳创智吴伟 | R24 miss | R03 / R20 **ok** |

→ 变更前：内容可召回，但 latest 硬锁期次导致 0 hit。  
→ 变更后：`latest_published` + recent → 跨已发布 + TimeWindow；R02/R23/R24 不再当 FTS 缺陷。

---

## 总览：8 道非 ok

| ID | 粗标签 | Likely layer | Phase 1 归属 |
|----|--------|--------------|----------------|
| R02 | issue_scope_latest_miss | **Scope / Product ✅** | 已落地；验证样本，非 Recall 缺陷 |
| R23 | issue_scope_latest_miss | **Scope / Product ✅** | 同上 |
| R24 | issue_scope_latest_miss | **Scope / Product ✅** | 同上 |
| R11 | partial | **FTS / Query**（4106 未进 Top20） | Recall 候选 |
| R12 | partial | **混合：FTS miss(4125) + Ranking(4120@6,4128@9)** | 4125→Recall；4120/4128→③ |
| R18 | partial | **FTS / Query**（4271 未进 Top20） | Recall 候选 |
| R13 | total_miss | **FTS / Query**（问句与条目措辞漂移） | Recall 候选 |
| R14 | total_miss | **Index / Data 先核**（播数据条） | 先核查索引 |

**防误区：** Top20 已含 relevant、仅 Top5 没有 → **算 Ranking，不算 Recall 优化。**  
本轮纯 Ranking 分量主要在 **R12 的 4120/4128**；其余 missed 多数 **根本不在 Top20**。

Vector：本基线 `used_vector=False`，**无法把失败归因到 Vector**；若要区分 Vector，需另开 `MESH_RECALL_USE_EMBED=1` 对照跑（仍属 Review，不改实现）。

脏 Issue Time：`2026-8-17` 的 `date_from/to=2026-09-07` 属 **Data Time Integrity**；本期这些题仍扫到该期条目，**不是本 8 题空结果的主因**（R02/R23/R24 空是因为 slug=2026-09-08）。

---

## 逐题

### R02 — 锦涛最近做了什么

| 字段 | 内容 |
|------|------|
| Query | 锦涛最近做了什么 |
| Expected relevant | 4251, 4259, 4260（2026-8-17 · 品牌创意 · 锦涛） |
| Retrieved Top20 | `[]`（n_hits_raw=0） |
| Missed | 4251, 4259, 4260 |
| Issue scope | `latest_published` → **2026-09-08**（date 2026-09-08～09-08） |
| Retrieval path | hybrid · FTS-only（no vector） |
| Failure class | `issue_scope_latest_miss` |
| Likely layer | **Scope / Product**（IssueRef=latest 把检索锁在当期；相关 item 在旧期） |
| Recommended action | **不要先改 FTS。** 产品决策：「最近」+ latest 是否允许跨期 / multi-issue；或要求用户显式选期。对照 R01/R25 explicit 已能召回。 |

### R23 — 锦涛提供的建站风格参考（latest）

| 字段 | 内容 |
|------|------|
| Query | 锦涛提供的建站风格参考 |
| Expected | 4251 |
| Retrieved Top20 | `[]` |
| Missed | 4251 |
| Issue scope | latest → **2026-09-08** |
| Path | hybrid · no vector · 0 hits |
| Failure class | `issue_scope_latest_miss` |
| Likely layer | **Scope / Product** |
| Recommended action | 同 R02；对照 **R25**（同 query + explicit:2026-8-17）R@20=1.0 → **非 FTS 无能**。 |

### R24 — 破壳创智吴伟拜访计划（latest）

| 字段 | 内容 |
|------|------|
| Query | 破壳创智吴伟拜访计划 |
| Expected | 4356 |
| Retrieved Top20 | `[]` |
| Missed | 4356 |
| Issue scope | latest → **2026-09-08** |
| Path | hybrid · no vector · 0 hits |
| Failure class | `issue_scope_latest_miss` |
| Likely layer | **Scope / Product** |
| Recommended action | 同 R02；对照 R03/R20 explicit 可召回 4356。 |

---

### R11 — Agent 治理与合规 7 月 15 日

| 字段 | 内容 |
|------|------|
| Query | Agent 治理与合规 7 月 15 日 |
| Expected | 4105, 4106 |
| Retrieved Top20 | 4105 @1；其后多为 Agent/座舱相关，**无 4106** |
| Missed | **4106**（市场规模判断句） |
| Issue scope | explicit:2026-8-17（脏 date 窗 09-07，但仍命中该期） |
| Path | hybrid · no vector · 27 hits |
| Failure class | `partial_miss_rank_or_fts` |
| Likely layer | **FTS / Query**（4106 未进 Top20 → **不是 Ranking**） |
| Recommended action | Recall 候选：查 4106 文本是否缺「7月15日/合规」token；同义扩展或条目互链。**禁止**为此上 Rerank。 |

### R12 — 端侧模型与智能座舱

| 字段 | 内容 |
|------|------|
| Query | 端侧模型与智能座舱 |
| Expected | 4116, 4120, 4125, 4128 |
| Retrieved Top20 | 4116@2, 4120@6, 4128@9；**4125 不在 Top20** |
| Missed | 4125（整条）；4120/4128 在 Top20 但 **>Top5** |
| Issue scope | explicit:2026-8-17 |
| Path | hybrid · no vector · 40 hits |
| Failure class | `partial_miss_rank_or_fts`（**混类**） |
| Likely layer | **4125 → FTS/Query（Recall）**；**4120/4128 → Ranking（③）** |
| Recommended action | 拆开记账：Recall Phase 1 只盯 4125 为何未召回；Top5 外的 4120/4128 **移交 Ranking**，不计入「要改 FTS」的理由。 |

### R18 — Founder Park 下半年规划

| 字段 | 内容 |
|------|------|
| Query | Founder Park 下半年规划 |
| Expected | 4358, 4271 |
| Retrieved Top20 | 4358@1；**无 4271**（AGI 传播复盘 / Founder Park 涨粉） |
| Missed | 4271 |
| Issue scope | explicit:2026-8-17 |
| Path | hybrid · no vector · 11 hits |
| Failure class | `partial_miss_rank_or_fts` |
| Likely layer | **FTS / Query**（4271 未进 Top20；问句偏「下半年规划」，4271 偏「传播复盘」） |
| Recommended action | Recall 候选：放宽 Gold（4271 是否必须 relevant）或 query/实体共现；非 Ranking。 |

---

### R13 — 具身智能数据圆桌讨论了什么

| 字段 | 内容 |
|------|------|
| Query | 具身智能数据圆桌讨论了什么 |
| Expected | 4154, 4155, 4156 |
| Retrieved Top20 | 4212, 4225, …（具身/活动相关他条，**无 expected**） |
| Missed | 4154, 4155, 4156 全部 |
| Issue scope | explicit:2026-8-17 |
| Path | hybrid · no vector · 40 hits |
| Failure class | `total_miss` |
| Likely layer | **FTS / Query**（「数据圆桌」与条目措辞未对齐，漂到其他具身条目） |
| Recommended action | Recall 候选：核对 4154 全文关键词；query 改写策略属 Phase 1 讨论项，**先证据后改**。 |

### R14 — 视频号本周播放较好的片子

| 字段 | 内容 |
|------|------|
| Query | 视频号本周播放较好的片子 |
| Expected | 4408–4412（播放数据条） |
| Retrieved Top20 | 仅 **4445** |
| Missed | 4408, 4409, 4410, 4411, 4412 |
| Issue scope | explicit:2026-09-08 |
| Path | hybrid · no vector · 7 hits |
| Failure class | `total_miss` |
| Likely layer | **FTS / Query 或 Index**（「播放/片子」可能未命中「播放 N、点赞」类短句条目；需确认 item_facts/FTS 是否收录这些 T11 条） |
| Recommended action | Recall 候选：先 **Data/Index 核查** 4408 是否在 search 语料；再谈 FTS。不是 Ranking。 |

---

## Failure → Layer 汇总（给 Phase 1 决策用）

| Likely layer | 题 | 建议 |
|--------------|----|------|
| Scope / Product | R02, R23, R24 | **先定产品语义**，勿当 FTS bug 修 |
| FTS / Query | R11(4106), R12(4125), R13, R18(4271), R14? | Recall Phase 1 **候选** |
| Ranking（③） | R12 中 4120@6, 4128@9 | **移交 Ranking**，不改 Retrieval 抬 Top5 |
| Vector | （本基线未开） | 可选对照跑，再归因 |
| Data / Index | R14 优先核查 | 先证实是否进索引 |
| Data Time Integrity | 脏 date 标注 | 独立修数，不混进 Recall |

---

## Recall Phase 1 准入建议（仍未开工）

仅在 Review 签字后，才允许改动，且应 **按层拆 PR**：

1. **Scope 策略**（若产品确认「最近」可跨期）— 非 FTS  
2. **Index 缺口**（若 R14 未进语料）— Data/Index  
3. **FTS/Query**（R11/R12/R13/R18）— 真正的 Recall  
4. **Ranking**（R12 Top5）— 进 ③，不进 Recall Phase 1  

**当前：Failure Pattern Review = GO 完成。**  
**当前：任何 RAG/Chunk/Vector/FTS/Rerank 改动 = NO-GO。**
