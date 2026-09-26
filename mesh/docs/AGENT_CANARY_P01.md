# 飞书 AI 回答质量 · 采集与复查（P0.1）

> **日期：** 2026-09-23
> **入口：** `/admin/qa`（editor+）
> **代码：** `app/qa_log.py`（采集）+ `app/agent/feishu_bot.py` / `app/agent/harness.py`（写入）+ `templates/qa_log.html`（回看）
> **原则：** 先建可复现的失败样本，再谈修哪一刀。质量仍冻结，本页只收集、不修。

---

## 为什么要有这一步

质量解冻的前提是「**可复现的答错 / 无证据乱答**」。此前线上飞书问答只写 `ask_log`
（`query / mode / n_hits / latency`），**没有答案文本、证据、answer_status**——
出问题无法回看，也就无法形成失败样本。

本页把每轮问答落库，补上这条证据链。

## 记录了什么

| 层 | 字段 |
|----|------|
| 事实层 | `answer_status` · `evidence_count` / `evidence_refs` · `claim_bindings` · `tools_called` · `error` |
| 对话层 | `question` · `answer_text`（display_text）· `intent` · `route` · `latency_ms` |
| 定位 | `request_id` · `source` · `channel` · `feishu_open_id` · `session_id` · `chat_id` · `trace_json`（精简） |
| 人工 | `feedback`（good/bad）· `feedback_note` · `feedback_by` · `feedback_at` |

落表 `agent_qa_log`。写入失败**绝不影响回复**（全 try/except，仅记日志）。

## 来源隔离（防自污染）★

**评测脚本一律走 `app.agent.harness.run_harness`**（`run_djt_full_smoke` / `run_temporal_baseline` /
`run_agent_llm_e2e` / `/api/agent/v1/message` 压测…）。若不隔离，在 tmesh/prod 容器里跑评测
会把**测试问句当成真实流量**写进质量表，样本库立刻失去可信度。

| source | 何时落库 |
|--------|----------|
| `feishu` | **默认落库**（`feishu_dm` / `feishu_group` 真实流量） |
| `eval` | **默认不落**；`MESH_QA_LOG_EVAL=1` 才落（`channel=harness`） |
| `http` | **默认不落**；`MESH_QA_LOG_EVAL=1` 才落（`channel=web` 等） |

- 全局关：`MESH_QA_LOG=0`。
- `/admin/qa` 默认 `source=feishu`，评测样本需显式切来源才看得到。
- 概览里的「全表来源分布」不受筛选影响，便于一眼确认有没有评测混入。

## 自动 flag（只排优先级，不替代人判断）

| flag | 触发 |
|------|------|
| `error` | 有 error |
| `refused` | 拒答 |
| `unsupported` | `answer_status=unsupported` 或任一 claim binding 为 unsupported |
| `weak` | `answer_status=weak` 或任一 claim binding 为 weak |
| `no_evidence` | 状态 unknown/空 **且** 无证据 |
| `unknown` | 状态 unknown 但有证据 |

## 怎么用

1. 飞书正常提问（或走 `POST /api/agent/v1/message` harness）。
2. 打开 `/admin/qa`：按渠道 / 状态 / 关键词筛，勾「只看我标了差」。
3. 逐条读「问 / 答 / 证据 / trace」，标 good 或 bad + 备注。
4. **本轮只收集，不修。** 等重复共性出现，再按 failure 模式集中打一刀。

## 复查清单（第一性指标）

沿用 `colleague_stage2a_blind.md` 的对话层口径，叠加事实层：

- 事实层：答案的核心 claim 是否有对应 evidence？`answer_status` 是否与内容相符？
- 对话层：像不像同事？长度是否自然？用户是否愿意继续追问？
- 归因：失败属于 Scope / Retrieval / Ranking / Claim / Answer / Persona 哪一层？

## 边界

- 只读 + 人工标注；**不触发** Preview / Ask / Embed / Publish。
- 不存原始 payload，只存精简 trace。
- **评测/HTTP 默认不落真实样本库**（见上「来源隔离」）；生产评测仍遵守 `PROD_DATA_POLICY.md` 只读约束。
- 多实例 Session 仍是放量门槛（见 `PROJECT_STATUS.md`）；本页不受其影响。
