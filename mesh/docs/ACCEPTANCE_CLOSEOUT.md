# 验收收尾分析（2026-08-31 · Planner-lite 全链路）

> 基于 `eval/reports/report_20260831_032953.json`（Golden）与 Prod 容器跑批（2026-08-31 11:23）。  
> 代码变更：**Query Guard**（Phase 1）+ **Planner-lite**（Phase 2）+ **KG v0 evidence**（publish 链）。

---

## 1. 全链路验收汇总

命令：

```bash
python eval/run_final_eval.py --corpus golden --e2e --followup --sse
# Prod 容器内：
python eval/run_final_eval.py --corpus prod --e2e --followup --sse
```

| 维度 | Golden | Prod PG |
|------|--------|---------|
| 25 题检索 | **25/25** | **25/25** |
| E2E LLM（e02/e03/e05） | **3/3** | **3/3** |
| 二轮 follow-up | **PASS** | **PASS** |
| SSE 断线回放 | **PASS** | **PASS** |
| failures | **[]** | **[]** |

---

## 2. Phase 1 回顾（Query Guard · 仍有效）

| Case | 行为 |
|------|------|
| e20/e25 | `mode=guard`，`n_hits=0`，`direct_answer`，检索前拦截 |

模块：`ask_query_guard.py` → `ask_engine.prepare` 在 `retriever.retrieve` 之前。

---

## 3. Phase 2 — Planner-lite 验收

### 3.1 实现

| 项 | 内容 |
|----|------|
| 模块 | `app/ask_planner.py` — `plan_retrieval(q, context_refs) → RetrievalPlan` |
| 挂载 | `ask_engine.prepare` 替换裸调 `parse_intent` |
| 原则 | **纯规则**，无 ReAct / LLM Planner；**未改** `qa_structured.parse_intent` 本体 |

### 3.2 原失败项 → 现状态

| ID | 问句要点 | 原问题 | 现 mode | set_op | Golden n_ctx |
|----|----------|--------|---------|--------|--------------|
| **e08** | 跟进了 / 还没接触 | 「跟进了」未匹配 diff | structured | diff | 25 |
| **e10** | 各团队…硬件话题 | topic 语序 | structured | by_team | 30 |
| **e21** | 可同步关系…重叠 | 「重叠」未触发 intersect | structured | intersect | 8 |
| e09 | 海外/国内缺口 | （已通过 legacy） | structured | overseas_gap | 28 |
| e01 | 同时也是 | （已通过 legacy） | structured | intersect | 0 |

### 3.3 Follow-up 不被误判

| ID | routing | mode | Planner 行为 |
|----|---------|------|--------------|
| e04 | followup | hybrid | refs 有 chunk → 跳过 structured |
| e16 | followup | hybrid | 同上 |
| e17 | followup | hybrid | anaphora「他们对比一下」→ 非 structured |
| follow-up E2E | Q1→Q2 | hybrid | context_refs 继承；Q1 Answer 不泄漏 |

### 3.4 Hybrid 题行为未变

| ID | mode | n_hits | llm_calls (Golden E2E) |
|----|------|--------|------------------------|
| e02 | hybrid | 40 | 5（与 Planner 前相同） |
| e03 | hybrid | 40 | 5 |
| e05 | hybrid | 40 | 5 |
| e15 | hybrid | 40 | 未误走 intersect |

**结论**：Planner 仅在独立问句 + 高置信 set_op 时切 structured；**无额外 LLM 调用**，hybrid 路径与 Phase 1 一致。

### 3.5 低置信 fallback

- topic 过短（如「关于AI各团队…」）→ `confidence=0.65` → **hybrid** + 「未做集合运算」说明
- 单测：`tests/test_ask_planner.py::test_low_confidence_by_team_fallback`

---

## 4. SSE 验收（仍 PASS）

| 检查项 | blocking | 结果 |
|--------|----------|------|
| disconnected_at_token | ✅ | before_first_token |
| analysis_id | ✅ | 持久化 |
| store_answer len>20 | ✅ | 568 chars |
| store_context_refs | ✅ | entities/teams/issues |
| store_sources + evidence | ✅ | evidence_in_sources=True |
| status=completed | ✅ | |
| api_replay | ✅ | HTTP 200，二次读一致 |
| token prefix 一致 | ❌ | 不要求 |

---

## 5. KG v0 Evidence（Prod 抽检 · 与 Planner 并行）

| 项 | 结果 |
|----|------|
| merge → publish | 41/41 relations 带 `evidence[]` |
| item/snippet 回溯 | 抽检 PASS |
| 旧 published_json | 无 evidence（仅新 merge 路径写入） |
| **已知边界** | LLM `body/details` 可超出 120 字 snippet（3/5 抽检卡） |

→ **Evidence v0 保持不动**； narrative 约束列入 **Phase 3 Verify v2**。

---

## 6. 已知限制（锁定）

1. Planner 规则表有限 — 新句式需加规则或走 hybrid+fallback
2. structured 零结果 — 固定 `empty_result_answer`，不调 LLM（e01 等）
3. LLM E2E 答案措辞可变 — eval 不断言全文一致
4. Prod e02 E2E llm_calls=3 vs Golden 5 — 语料/命中差异，均 pass
5. **未做** Retrieval confidence gate（hybrid 弱命中直出 direct_answer）
6. **未做** Ask 读 `published_json.relations[].evidence`

---

## 7. 里程碑状态

| 阶段 | 状态 |
|------|------|
| Phase 1 Query Guard + eval 骨架 | ✅ |
| Phase 2 Planner-lite | ✅ 全链路 PASS |
| KG v0 evidence JSON | ✅（Prod 误 publish 已 **回滚 v1**） |
| **Phase 3 Verify v2** | ⏳ **代码已合入** — relation/claim/citation |
| 下一期真实 publish | 待 owner 流程验证 evidence 链 |

---

## 8. Prod 数据回滚（2026-08-31）

| | 回滚前 | 回滚后 v1 |
|--|--------|-----------|
| published_at | 2026-08-31 11:05 | 2026-08-27 09:54 |
| relations | 41 | **14** |
| weak | 0 | **4** |
| evidence | 41 条 | 0（v1 无 evidence 字段） |

详见 `docs/PROD_DATA_POLICY.md`。**禁止** Prod regen+publish 抽检。

---

## 9. Phase 3 Verify v2 实现摘要

| 项 | 实现 |
|----|------|
| A. Relation | `relation_verify.verify_relation_narrative` — 删减无 evidence 支撑的 details；body 降级 skeleton |
| B. Ask claim | `_claim_supported` — evidence_refs → item_id/chunk_id + 2-gram overlap |
| C. Citation | `validate_answer` 返回 `bindings[{sentence, item_ids, chunk_ids}]` |
| 单测 | `tests/test_relation_verify.py`（正常/越界/无 evidence） · `tests/test_claim_verify_v2.py` |
| Golden | **25/25** 检索不回归 |

---

## 10. 下一步

1. 下一期周报走完整 **preview → merge → verify → owner → publish**
2. Prod **只读** eval + `--audit-only` 验证回滚后 14 卡
3. Verify v2 在真实 publish 后抽检 narrative + Ask bindings
4. **不做** ReAct / LLM Planner / schema 变更

---

## 11. 报告与脚本索引

| 产物 | 路径 |
|------|------|
| Golden 全链路 JSON | `eval/reports/report_20260831_032953.json` |
| Golden MD | `eval/reports/FINAL_ACCEPTANCE_REPORT.md` |
| Prod 摘要 MD | `eval/reports/FINAL_ACCEPTANCE_REPORT.prod.md` |
| Phase 1 收口 | `eval/reports/PHASE1_CLOSEOUT_REPORT.md` |
| Planner 单测 | `tests/test_ask_planner.py` |
| KG Prod 抽检 | `deploy/prod_kg_spotcheck.py` |
