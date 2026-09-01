# Mesh Ask 最终验收报告（Prod PG · Planner-lite 全链路）

- 生成时间：2026-08-31T11:23:15
- 语料环境：**prod_pg**
- 生产 PG 全库：**是（多期 published）**

## 汇总

| 维度 | 结果 |
|------|------|
| 25 题检索 | 25/25 pass |
| E2E LLM | 3/3 e2e pass |
| 二轮 follow-up | PASS — 二轮 follow-up OK |
| SSE 断线回放 | PASS — SSE 回放 OK |

## E2E 延迟与用量

| ID | retrieve_ms | analysis_ms | total_ms | llm_calls | correctness |
|----|-------------|-------------|----------|-----------|-------------|
| e02 | 3647 | 14504 | 18151 | 3 | pass |
| e03 | 2526 | 51584 | 54110 | 5 | pass |
| e05 | 2561 | 24580 | 27141 | 5 | pass |

## Planner-lite 重点 case

| ID | mode | set_op | n_ctx |
|----|------|--------|-------|
| e08 | structured | diff | 5 |
| e10 | structured | by_team | 30 |
| e21 | structured | intersect | 6 |
| e04/e16/e17 | hybrid (followup) | — | 40 |
| e02/e03/e05 | hybrid (E2E) | — | 40 |

*完整逐题见容器内 `eval/reports/FINAL_ACCEPTANCE_REPORT.md`（2026-08-31 11:23 跑批）*
