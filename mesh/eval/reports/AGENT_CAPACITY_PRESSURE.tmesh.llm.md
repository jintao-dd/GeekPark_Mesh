# Agent Capacity / E2E Pressure Test

**target**=`http://127.0.0.1:8091` · **issue**=`2026-8-17` · **when**=`2026-09-09T07:55:36.030978+00:00`

- C_safe = `8`
- C_knee = `None`
- C_max = `None`
- baseline P95 = `137620.6` ms

## Levels

| C | n | success | err | 429 | timeout | P50 | P95 | P99 | TTFB P95 | rps | quality | evidence_ux | CPU after |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 100% | 0% | 0 | 0 | 137620.6 | 137620.6 | 137620.6 | 137620.5 | 0.007 | 100% | 100% | 0.26% |
| 2 | 2 | 100% | 0% | 0 | 0 | 60596.0 | 114140.2 | 118899.7 | 114140.1 | 0.017 | 100% | 100% | 0.30% |
| 4 | 4 | 100% | 0% | 0 | 0 | 89816.3 | 121853.0 | 122012.1 | 121852.8 | 0.033 | 100% | 100% | 0.23% |
| 8 | 8 | 100% | 0% | 0 | 0 | 42450.6 | 107060.9 | 118030.6 | 107060.8 | 0.066 | 100% | 100% | 0.24% |

## Classification notes

- 在测试阶梯内未观察到明显恶化；C_knee 未触发
- 在测试阶梯内未出现大量 timeout/429/error；C_max 未触发

## Heuristics

- **C_safe**: success≥99%, no timeout/429, quality≥95%, evidence_ux≥90%, P95≤2×(C=1)
- **C_knee**: first level failing C_safe
- **C_max**: success<90% or error>10% or timeout≥5% or any 429
