# Phase 3 Verify v2 验收报告

> 2026-08-31 · North Star 定稿 + Verify 扩展 + Owner 闸门  
> **生产 mesh：只读，未 publish** · **tmesh：verify 试跑，未 publish**

---

## 1. North Star（已写入 `docs/AGENT_EVOLUTION.md`）

```
Sources → Extract → KG (Entity/Relation/Evidence)
  → LLM 组织表达 → Validator 证明 → Owner 确认 → Published
```

- Sources 是唯一事实源
- LLM 不创造关键事实
- Published 可回溯：`narrative → evidence[] → item_id → source_id → 材料`
- Agent 未来 = orchestrator，非事实作者，不替 Owner publish

**不做**：ReAct / Full Agent / Neo4j / LLM Planner

---

## 2. 改动文件

| 文件 | 变更 |
|------|------|
| `docs/AGENT_EVOLUTION.md` | North Star + 字段盘点 + M3 里程碑 |
| `app/issue_verify.py` | **新增** — lead/keywords/plans/views/contacts verify |
| `app/relation_verify.py` | （已有）relations body/details ↔ evidence |
| `app/relation_candidates.py` | merge 末尾挂载 `verify_issue_draft` |
| `app/relation_gate.py` | `issue_publish_blockers` — 无 evidence / unsupported |
| `app/main.py` | publish 走 `issue_publish_blockers` |
| `deploy/run_phase3_acceptance.py` | **新增** — tmesh 试跑 + 回溯链抽样 |
| `tests/test_issue_verify.py` | **新增** — 5 cases |

---

## 3. 字段 evidence grounding 状态

| 字段 | 约束 | 模块 |
|------|------|------|
| `relations[].body/details` | ✅ | `relation_verify` + `owner_guard.filter_draft_relations` |
| `relations[].evidence[]` | ✅ item_id/source_id | `relation_candidates._build_evidence` |
| `lead` | ✅ 删 unsupported 句 | `issue_verify._verify_lead` |
| `keywords.*.rows[].v` | ✅ 删 unsupported 行 | `issue_verify._verify_grouped_section` |
| `plans.*.rows[].v` | ✅ | 同上 |
| `views[].text` | ✅ 删或降级 weak | `issue_verify._verify_views` |
| `contacts.*.rows`（非来源/身份） | ✅ | `issue_verify._verify_contacts` |
| `kpis` | ⏸ 未约束 | owner 目视 |
| `gaps` / `data_sources` | ⏸ 未约束 | 接入/缺口说明 |
| `question` | N/A | 模板固定 |

生成路径（未改 pipeline）：`pipeline → cards → llm.build_issue_draft → merge_relations_from_candidates → verify* → owner publish`

---

## 4. Owner 闸门（publish 拦截）

`relation_gate.issue_publish_blockers` + `main.publish_blockers`：

- `weak=True` 关系未确认 → **拦截**
- 跨团队无独立出处（同源同 pointer）→ **拦截**
- 强关系无 `evidence[]` → **拦截**
- `collect_unsupported_flags`：无 evidence 强关系、unsupported body/detail → **拦截**
- Verify 后的 skeleton body（「本期均有与…」）→ **放行**（已降级叙事）

---

## 5. 单测

```
tests/test_issue_verify.py          5 passed
tests/test_relation_verify.py       3 passed
tests/test_claim_verify_v2.py       4 passed
tests/test_owner_guard.py           5 passed
tests/test_golden_attribution.py    4 passed
────────────────────────────────────────────
合计                                21 passed
```

---

## 6. Golden 检索

```
python eval/run_final_eval.py --corpus golden
→ 25/25 pass
```

（Ask 轨 Verify v2 回归未回退）

---

## 7. tmesh 完整链路（dry-run，未 publish）

环境：`geekpark-tmesh` · DB `tmesh` · slug `2026-8-17`

```bash
docker exec -w /srv/mesh geekpark-tmesh python deploy/run_phase3_acceptance.py --slug 2026-8-17 --sample 3
```

| 项 | 结果 |
|----|------|
| merge + relation_verify + issue_verify | ✅ 跑通 |
| corpus_chars | 43601 |
| keywords/views trim（本期稿） | 0 行删减（已 grounded 或空） |
| publish blockers（dry-run） | 1× weak 待确认（字节豆包） |
| 生产 publish | **未执行** |

> tmesh DB 为 prod 克隆快照，含完整 items/evidence；与 prod 当前 v1 稿可能不同。

---

## 8. 抽样回溯链（narrative → evidence → item → source）

### 卡 1：面壁智能 · 詹杨帆

| 层 | 内容 |
|----|------|
| **narrative** | detail: `编辑部记录：面壁智能舱内方案…` |
| **evidence** | snippet 同条 · `item_id=674` · team=编辑部 |
| **item** | `source_id=23` · pointer=`面壁智能—詹杨帆 · 二` |
| **source** | `编辑部沟通记录 · 与面壁智能詹杨帆的对话` |

### 卡 2：AGI Playground 嘉宾资源

| 层 | 内容 |
|----|------|
| **narrative** | body: skeleton「品牌创意团队、视频号团队本期均有与…」 |
| **evidence** | `item_id=822` · AGI 2026 复盘 snippet |
| **item** | `source_id=24` · pointer=`妙记 56:04` |
| **source** | `品牌创意团队周会复盘` |

### 卡 3：Founder Park · AGI 2026

| 层 | 内容 |
|----|------|
| **narrative** | body: skeleton + details 由 evidence 展开 |
| **evidence** | `item_id=628` · Founder Park 下半年规划 snippet |
| **item** | `source_id=22` · pointer=`Part 4 目标对齐` |
| **source** | `社群例会提及` |

完整 JSON 见 tmesh 命令 stdout 或重跑 acceptance 脚本。

---

## 9. 已知限制 & 下一步

1. **kpis/gaps/data_sources** 仍未做 token-level verify（owner 目视）
2. **tmesh 与 prod 数据分叉** — prod 已手工修詹杨帆假消息；tmesh 克隆体仍含历史条目，应用新 verify 重新出草稿后再 owner 审
3. **Agent 插入点** — 等 M3 tmesh 全链路 owner 试 publish 稳定后再讨论（orchestrator 在 merge/verify 之间，非写稿）

---

## 10. 生产政策

- **mesh 生产**：只读 eval / 不 script publish（`PROD_DATA_POLICY.md`）
- **tmesh**：`MESH_ALLOW_PROD_PUBLISH=1`，用于 regen / 全链路试跑
