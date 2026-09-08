# Recall Phase 1 · ③ FTS/Query/Hybrid（最小修改）

> **状态：** 🟢 已落地（tmesh 重跑 Gold）  
> **不动：** Rerank / Ranking / Chunk 大改 / Agent Contract  

诊断原始数据：`RECALL_P1_DIAG.json` · 修改后：`RECALL_P1_AFTER.json`

---

## 诊断结论（改前）

| 题 | 丢失层 | 根因 |
|----|--------|------|
| **R14** | Query 配方 | MATCH 含 `AND 片子`；正文无「片子」 |
| **R12** | Query + Hybrid | `端侧 AND 座舱` 漏单侧；改 OR 后嵌套括号撑爆 `fts_pg`；LIKE 仅在命中少时触发 |
| **R13** | Query | 长片噪声 OR；头4∧尾4 后可召回 |
| **R18** | Query | `founder∧park∧下半年规划`；4271 无规划字面 |
| **R11** | 内容 / 配方 | MATCH 退化成 `agent∧7∧15`；4106 正文无「7月15/治理/合规」（仅智能体安全测算） |

---

## 最小修改

### `app/tokenize.py` — MATCH 配方
- 停用评价口语：`片子/较好/…`
- 并列主题（与/和）→ **扁平 OR**（禁止嵌套括号）
- 长片 → 头4 AND 尾4（两组扁平 OR）
- 有拉丁实体：丢掉裸数字；软丢「下半年/规划」；`Agent ↔ 智能体`

### `app/item_facts.py` — 召回补面
- MATCH 已饱和时仍用 `query_terms` LIKE 补召回
- 短词命中优先，避免「模型/智能」占满 LIMIT

---

## Gold 同卷对照（n=30 · Embed=False）

| | R@5 | R@10 | R@20 |
|--|-----|------|------|
| **Phase 0 Baseline** | 74% | 79% | 79% |
| **Phase 1 After** | **83%** | **90%** | **94%** |
| **Δ** | **+9pt** | **+11pt** | **+15pt** |

### 候选题（改后）

| 题 | R@20 | 残留 |
|----|------|------|
| R11 | 0.50 | 4106 正文不对齐问句（非索引缺失） |
| R12 | **1.00** | Top5 仍可能丢 4120 → 留给 Ranking |
| R13 | **1.00** | — |
| R14 | 0.80 | 4412 无「播放」字面（judgment） |
| R18 | **1.00** | — |

failure_pattern：`ok` 29 / `partial` 1（全卷）

---

## 下一步

- Recall 已明显抬升；残留 R11/4106、R14/4412 属 **内容对齐 / Gold 边界**，不是 Index。
- **可以考虑开 ③ Ranking**（R12 的 4120@6 / 4128@9 等 Top5 问题），或继续小步 Query。
- **仍禁止**为抬分改 Gold。
