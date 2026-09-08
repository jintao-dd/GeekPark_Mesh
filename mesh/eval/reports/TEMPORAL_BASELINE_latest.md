# Temporal Phase 0 · Baseline

- Gold: `temporal_gold_v1.jsonl` · **n=24**（A–F 各 4）
- Pass（措辞启发式）: **22/24** (92%) — **不要求全过**
- Elapsed: 129s · tmesh `--reuse-env-db` · `MESH_AGENT_USE_LLM=1`
- 报告：`TEMPORAL_BASELINE_latest.json`

> Phase 0 完成条件已满足：**Gold 建完 → Baseline 跑通 → 得到失败分布 → 确认问题边界**。  
> **下一步才是最小 Temporal 实现（仍不改 RAG）。**

---

## 解读（比 22/24 更重要）

启发式 PASS ≠ 时间语义正确。大量「PASS」其实是：

1. **未命中内容** → 模板「未找到」→ 碰不到「最近发生」禁词 → 假阴性偏低  
2. **措辞止血已生效** → 部分题已有「依据 ×× 期…并非今日」  
3. **真失败仍暴露边界**

### 已确认的问题边界

| 模式 | 例 | 说明 |
|------|----|------|
| **墙上时钟「上周」** | T06 FAIL | 回答用 `2026-08-31～2026-09-06`，相对「今天」而非 Issue/Event |
| **刚发生 / 最近发生** | T14 FAIL | 明示问「是不是刚发生」时仍出现违规确定性措辞（虽有部分 caveat） |
| **脏 Issue Time** | T13 等 | `2026-8-17` 的 `date_end=2026-09-07`，检索窗打印成 9/7；材料实为 8 月 |
| **latest 无该实体** | T01 PASS(miss) | 「锦涛最近…」钉在 `2026-09-08` → 未找到；**经典错案未在 latest 上触发**（内容在旧期） |
| **latest ≠ recent** | T15/T16 | 多未命中或未正面等同；需最小实现后强制拒答口径 |

### Failure reason histogram

- `forbid_wall_clock_last_week`: 1（T06）
- `forbid_recent_event_claim`: 1（T14）
- `forbid_just_happened`: 1（T14）

---

## Per-item

- **T01** [A] PASS · miss on latest `2026-09-08` · Q: 锦涛最近做了什么
- **T02** [A] PASS · miss · 破壳创智最近有什么进展
- **T03** [A] PASS · issue caveat · 近期哪些公司第一次进入记录
- **T04** [A] PASS · 视频号最近在跟什么选题
- **T05** [B] PASS · 本周编辑部关注了什么
- **T06** [B] **FAIL** · `forbid_wall_clock_last_week` · 上周商业化…
- **T07** [B] PASS · 本周有没有可同步的关系
- **T08** [B] PASS · 上周视频号数据怎么样
- **T09** [C] PASS · explicit `2026-8-17`
- **T10** [C] PASS · explicit `2026-09-08`
- **T11** [C] PASS · 八月下旬社群例会
- **T12** [C] PASS · OdyssLife @ 2026-09-08
- **T13** [D] PASS · miss（窗显示 2026-09-07）· 锦涛@explicit 2026-8-17
- **T14** [D] **FAIL** · `forbid_recent_event_claim;forbid_just_happened`
- **T15** [D] PASS · miss · 八月社群算最近发生吗
- **T16** [D] PASS · miss/拒 · 最新上线当成这周刚发生
- **T17–T20** [E] PASS（启发式）
- **T21–T24** [F] PASS · Issue 钉定期次

---

## 最小实现前不要做的事

- ❌ 优化 RAG / Chunk / Rerank  
- ❌ 为提高 22→24 去改检索  

## 最小实现应针对

1. Time Intent / Basis / Filter（Hard Rules 三条）  
2. 「上周/本周/最近」禁止落到墙上时钟  
3. unknown / 无 event_time → 禁「刚发生/最近发生」  
4. 修或暴露脏 `date_start/end`（数据问题，与语义规则分开记）  
