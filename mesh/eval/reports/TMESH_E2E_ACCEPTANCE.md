# tmesh E2E 验收报告

- **时间**：2026-08-31（UTC+8）
- **环境**：`tmesh.geekpark.net` · slug `2026-8-17` · 真实语料（7 sources / 284 items）
- **链路**：sources → pipeline merge → **preview（LLM 要点卡+草稿，401s）** → regen merge + verify → owner 审核 → **publish** → reindex → Ask eval
- **总结果**：**CONDITIONAL PASS**（Verify/Owner/Publish/Evidence/生产隔离 ✅ · Ask tmesh 21/25 ⚠️）

---

## 1. preview 前后数据变化

| 指标 | Preview 前（prod v1 快照） | Preview + verify 后 |
|------|---------------------------|---------------------|
| relations | 14 | 41 |
| weak | 6 | 2 → owner confirm 后 0 |
| with evidence | 0 | **41（100%）** |
| lead_len | 225 | 220 |
| n_views | 6 | 3（verify trim） |
| n_contacts_blocks | 4 | 4 |
| n_sources / n_items | 7 / 284 | 7 / 284 |

**verify trim（`_verify` meta）**

```json
{
  "keywords_rows_trimmed": 0,
  "plans_rows_trimmed": 0,
  "contacts_rows_trimmed": 0,
  "views_trimmed": 0,
  "lead_ok": true
}
```

Preview job：`done=true`，9 张要点卡 + 草稿 LLM，耗时 **401s**。

---

## 2. publish blockers（Verify v2 拦截）

### Owner 前（4 类）

1. **weak 待确认**（2 条）：张鹏 · AGI Playground 2026、苹果 Mac · 供应链话题
2. **body unsupported**（3 条）：具身智能 · 人形机器人、千问 · 智能体动态、王兴兴 · 宇树内容双出口

> 无 `no-evidence` 强关系（merge 后 41/41 均有 evidence[]）；blockers 正确区分 weak / unsupported。

### Owner 操作

| 动作 | 结果 |
|------|------|
| **confirm weak**（有 evidence） | confirmed=2, dropped=0 |
| **reject unsupported**（删卡） | dropped=3（上述 3 条 body unsupported） |

### Owner 后 blockers

- **[]（空）** → 允许 publish

---

## 3. publish 结果

| 字段 | 值 |
|------|-----|
| status | `published` |
| published_at | `2026-08-31 12:59` |
| relations | **38**（41 − 3 删卡） |
| with evidence | **38 / 38** |
| weak | 0 |

**audit 抽检（seed=31, n=3）**

- evidence_item: 10/10 pass
- snippet_match: 10/10 pass
- body_supported: 3/3 pass
- **audit PASS: true**

---

## 4. evidence 回溯抽样

### 样例 A：张鹏 · AGI Playground 2026

```
narrative.body → evidence[822].snippet
  → item_id=822, pointer="纪要 56:04", team=品牌创意团队
  → source_id=24, title="品牌创意 部门周例会(测试)"
  → source 原文 head: "08-11 | 部门周例会..."
```

### 样例 B：面壁智能 · 端侧模型与芯片适配

```
detail → evidence[688].snippet（硅谷 BD 团队记录）
  → item_id=688, source_id=23
  → source 原文含「内容中心-数据聚合」飞书导出

detail → evidence[668]（编辑部记录120）
  → 跨团队 evidence 均可在 items + sources 回溯
```

完整 JSON 见 `eval/reports/tmesh_e2e_report.json` → `evidence_traces` / `audit.samples`。

---

## 5. 页面检查

| 检查项 | 结果 | 说明 |
|--------|------|------|
| HTTP | 200 | `http://tmesh.geekpark.net/2026-8-17` |
| lead / relation 片段 | 未命中 | **tmesh 读者页需登录**（curl 返回登录 gate，非 published 内容） |
| published_json ↔ 页面 | 未直接比对 | 需登录态人工抽检；JSON 已 audit PASS |

---

## 6. Ask 回归

| 环境 | 结果 | 说明 |
|------|------|------|
| **tmesh**（reindex 后） | **21/25** | 失败：e08/e09/e10/e21（structured `set_op`） |
| **prod**（对照，只读） | **25/25** | 生产未改动，基线不变 |

失败题均为 structured 集合运算（diff / overseas_gap / by_team / intersect），与 tmesh 本次 publish 的 **38 关系卡**（vs prod v1 的 14 卡）图谱差异相关，**非 Verify v2 回归**。

---

## 7. 生产数据隔离

| 检查点 | Before | After |
|--------|--------|-------|
| prod status | published | published |
| prod published_at | 2026-08-27 09:54 | **2026-08-27 09:54（未变）** |
| prod relations | 14 | **14（未变）** |

tmesh 独立 DB（`geekpark-tmesh-pg` / database `tmesh`），本次所有写操作仅在 tmesh。

---

## 8. 结论与后续

### ✅ 通过项（可冻结 Verify v2）

- weak / unsupported / no-evidence **正确进入 publish blockers**
- owner **confirm weak** + **reject（删 unsupported 卡）** 流程正常
- published_json 中 relation / lead / keywords / plans / views / contacts **evidence grounding 保持**
- narrative → evidence → item_id → source_id → source 原文 **可回溯**（audit 10/10）
- **生产数据零修改**

### ⚠️ 待跟进（不阻塞 Verify v2 冻结）

1. **Ask tmesh 21/25**：structured 题在 expanded relation 集上失败；prod 仍 25/25
2. **页面验收**：tmesh 需登录，建议 Owner 登录后目视确认 1–2 张关系卡

### 决策

**Verify v2 冻结。** 防护层已达预期；不再继续堆 verify 规则。

下一步：**重新评估 Agent 插入点与 Tool 设计**（orchestrator on Entity/Relation/Evidence，不替代 Owner publish）。

---

*原始数据：`eval/reports/tmesh_e2e_report.json` · 运行日志：`eval/reports/tmesh_e2e_run2.log` / `tmesh_e2e_run3.log`*
