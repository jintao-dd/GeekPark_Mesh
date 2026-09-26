# Full Regression Report · 2026-09-02（部署后终版）

## 总览

| 环境 | pytest | Ask 25 | Relation 核心指标 | 部署 |
|------|--------|--------|-------------------|------|
| **本地** | **186/186** | **25/25**（golden） | 单测 32/32 | 最新代码 |
| **tmesh** | — | 20/25（PG 仅 1 期 published） | **strong_ev=0 · team_mis=0 · blocked=0** | **已同步 + migrate 1.8.4** |

---

## 1. 本地

| 指标 | 结果 |
|------|------|
| pytest | **186/186** |
| Ask 25（golden SQLite） | **25/25** |
| Relation 单测 | **32/32** |
| Embedding 解耦单测 | **5/5** |

---

## 2. tmesh 部署

- 已同步：`app/`、`deploy/`（含 readiness / integrity / migrate）、`eval/`（含 run_final_eval）
- `db.migrate` → **schema 1.8.4**（`embedding_status` / `embedding_model` 列已加）
- 容器已重启：`geekpark-tmesh` + `geekpark-tmesh-worker`
- Preview **完成**（~10min，19 张关系卡）

---

## 3. tmesh · Relation / Verify / Integrity

**Issue `2026-8-17` · draft（preview 后）**

| 指标 | 结果 | 目标 |
|------|------|------|
| 关系卡 | 19 | — |
| Decision coverage | **60/60，missing=0** | ✅ |
| **strong 无 evidence** | **0** | ✅ 100% |
| **team_mismatch** | **0** | ✅ |
| **blocked leakage** | **0** | ✅ |
| KPI 与卡数一致 | ✅ | ✅ |
| 全部有 evidence | **19/19** | ✅ |
| meta route copy | **0** | ✅ |

### reader_visible / draft backlog

| 字段 | 值 |
|------|-----|
| `n_reader_visible` | **19** |
| `n_draft_backlog` | **0** |
| `all_grounded` | **true** |

### Integrity scan（5 条余量）

- **14/19** 完全通过扫描
- **5 条** `snippet_not_in_item`（evidence 存在，snippet 与 item 文本 HEAD 不完全匹配）
- **无** team_mismatch / blocked / 缺 evidence

### Verify publish blockers（仍 6 条，预期）

上线前闸门仍会拦：团队证据不足、body 无法由 evidence 证明等 — **不属 embedding 回归**。

---

## 4. tmesh · Ask 25

```
eval/run_final_eval.py --corpus prod
→ 20/25 pass
```

**原因**：tmesh PG 当前仅 **1 期 published**（`2026-08-21`），缺 `2026-8-17` 索引语料；失败题为面壁/詹杨等 **2026-8-17 专属**（e02/e05/e06/e07/e11）。

**对照**：同一套 eval 在本地 golden（2026-8-17 语料）为 **25/25** — Ask 引擎未回归。

---

## 5. 新字段验收

| 字段 | tmesh 现状 | 说明 |
|------|-----------|------|
| `decision_tier` | **strong 6 · parallel 12 · watch 1** | ✅ Preview 关系路径已验证（`no_tier=0`） |
| `reader_visible` | 19/19 | ✅ |
| `draft backlog` | 0 | ✅ |
| `embedding_status` | 空（draft 未 publish） | 列已就绪；publish 后会 `pending` → worker 补齐 |

---

### decision_tier · tmesh 验收（2026-09-02 12:55）

- 代码已同步至 `geekpark-tmesh` + worker
- 完整 Preview LLM 因模型接口 **524** 未重跑；用现有 audit + narrative **重走 `build_relations_two_phase` 真实路径**（同 Preview 内 `merge_relations_from_candidates`）
- `_publish_readiness.py 2026-8-17`：`tier_counts` **strong 6 / parallel 12 / watch 1**，`structural_issues` **[]**，audit `by_tier` 一致
- 离线 replay：`strong 11 / parallel 26 / watch 18`（全 gate-pass 卡，含未进 draft 的候选）

---

## 6. 结论

### 已通过（本轮修改未打坏）

- Publish 与 Embedding 解耦（schema + 代码已上 tmesh）
- Relation evidence / team / blocked 核心指标
- Decision 全覆盖（60/60）
- 本地 pytest + Ask 全绿

### 已知差距（非本轮 embedding 回归）

1. **Ask tmesh 20/25** — PG 语料缺 2026-8-17 published 期（环境数据，非代码）
2. ~~**decision_tier 未落卡**~~ — **已修且 tmesh 验收通过**
3. **5 条 snippet_not_in_item** — evidence 校验严格度（可选优化 snippet 对齐）
4. **6 条 verify blockers** — 上线前人工/owner 处理

---

## 7. 建议下一步

1. 若要在 tmesh 验 Ask 25/25：将 `2026-8-17` publish 到 tmesh（或导入 golden 语料）
2. ~~补 `decision_tier` 写入~~ → **已完成**；在 tmesh 执行 `deploy/run_tmesh_regression_remote.sh 2026-8-17` 或等价 sync + preview + `_publish_readiness.py` 验 `tier_counts`
3. 正式 publish 一次验证 `embedding_status: pending → completed` 与 worker 队列
