# R14 Index/Data 核查（Phase 1 · ②）

> **只读。** 不改 FTS / Query / Chunk / Vector / Rerank。  
> 环境：tmesh · 2026-09-08  
> 原始数据：`eval/reports/R14_INDEX_CHECK.json`

## 目标

```
4408–4412
 ↓
是否存在于当前 Retrieval 使用的索引 / FTS / item_facts 等语料面
 ↓
确认是否真的可被检索
```

## R14

| | |
|--|--|
| Query | 视频号本周播放较好的片子 |
| Scope | `explicit:2026-09-08`（issue_anchor · date 2026-09-08～09-08） |
| Expected | 4408, 4409, 4410, 4411, 4412 |

### 同 scope 下 R14 原查询再探（只读）

| | |
|--|--|
| mode | hybrid · Embed=False |
| n_hits_raw | 7 |
| Top20 item_ids | `4445` only（expected 全 miss） |

---

## 逐 item

| item | items 表 | issue | status | item_facts | item_facts_fts | chunk_index | search_fts | 探针可搜？ | scope 排除？ |
|------|----------|-------|--------|------------|----------------|-------------|------------|------------|--------------|
| **4408** | ✅ | 2026-09-08 | published | ✅ | ✅ | ✅ | ❌ | ✅ item_facts（片名 top1；「视频号 播放」@2） | 否 |
| **4409** | ✅ | 2026-09-08 | published | ✅ | ✅ | ✅ | ❌ | ✅ item_facts（片名 top1；「视频号 播放」@1） | 否 |
| **4410** | ✅ | 2026-09-08 | published | ✅ | ✅ | ✅ | ❌ | ✅ item_facts（片名 top1；「视频号 播放」@3） | 否 |
| **4411** | ✅ | 2026-09-08 | published | ✅ | ✅ | ✅ | ❌ | ✅ item_facts（片名 top1；「视频号 播放」@4） | 否 |
| **4412** | ✅ | 2026-09-08 | published | ✅ | ✅ | ✅ | ❌ | ✅ item_facts（探针 top1）；「视频号 播放」未进 top40 | 否 |

### 内容摘要（证明是播放数据条）

| item | 文本要点 |
|------|----------|
| 4408 | 《AGI时代来了？》本周播放 7618、点赞 47…（T11 · 视频号团队） |
| 4409 | 《这款Harness震撼硅谷》本周播放 6429… |
| 4410 | 《AI巨头开始抢苹果饭碗》本周播放 3673… |
| 4411 | 《传统相机太难用！》本周播放 4270… |
| 4412 | 本周观众更关注 OpenAI/GPT6/AGI…（judgment，无「播放」字面） |

---

## 结论

### **A. 已进索引，但检索没召回 → FTS/Query**

依据：

1. **不是 B**：五条均在 `items` + `item_facts` + `item_facts_fts` + `chunk_index`；期次 published，与 R14 scope 一致。  
2. **不是 C**：无 draft/raw；slug=2026-09-08；`blocked=0`；无 date/slug 排除迹象。  
3. **可被检索**：用片名词或「视频号 播放」走 `item_facts.search` 可直接命中 4408–4411（4412 对「播放」弱匹配属预期）。  
4. **R14 原句仍 miss**：hybrid 只见 `4445`，expected 全不在 Top20 → 问题在 **Query / FTS·Hybrid 匹配形态**，不是「没进语料面」。

### 备注（仍属 A 的细节，不是 Data 缺口）

- 五条 **未进入 `search_fts` 正文轨**（LIKE 片名/正文 0 hit），主要挂在 **item_facts 轨**。Hybrid 本就会扫 item_facts，故「没进 search_fts」≠「没进 Retrieval 语料面」。  
- 下一刀若开 FTS/Query，应查：为何「视频号本周播放较好的片子」合并不进 Top20，而「视频号 播放」能进。  
- **禁止**因此上 Rerank / 改 Chunk / 接 Vector。

---

## 状态

| 步骤 | 状态 |
|------|------|
| ① Scope | ✅ DONE（勿再动） |
| **② R14 Index/Data** | ✅ **DONE → 归因 A** |
| ③ FTS/Query（含 R14 + 既有 7 miss） | ⬜ NEXT（R14 现正式可进 Recall 候选） |
| ④ 重跑 Retrieval Gold | ⬜ |
