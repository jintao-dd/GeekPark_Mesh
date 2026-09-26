# Temporal Phase 1 · 结果

> 2026-09-08 · tmesh · `temporal_gold_v1` 重跑  
> **范围：** Time Intent → Time Basis → Time Filter + 3 Hard Rules  
> **未做：** RAG / Rerank / v1 Contract 变更

## Delta

| | Phase 0 | Phase 1 |
|--|---------|---------|
| Pass | **22/24** (92%) | **24/24** (100%) |
| 真失败 | T06 墙上时钟上周；T14 刚发生 | （无） |
| 系统输出 | 无 | `trace.temporal`（basis/window/filter） |

## 实现落点

- `app/agent/temporal.py` — Intent / Basis / Filter / Hard Rules  
- `app/agent/adapters.py` — `ask.published` 接入（prompt + 成文后过滤）  
- `app/agent/runtime.py` — `trace.temporal`  
- `app/llm.py` + `prompts/qa.md` — 成文约束（非检索）

## 仍未解决（不挡 Phase 1）

- 多数「最近」题在 latest=`2026-09-08` **内容未命中**（召回问题 → ②）  
- `2026-8-17` 脏 `date_start/end` 仍显示 9/7（数据问题）  
- 尚无真实 `event_time` 字段（basis=unknown 为诚实默认）

## 下一步

Temporal 最小门禁已过 Gold 启发式 → 可进 **② Retrieval Recall**（另建 Recall Gold；不回头改 RAG 当「时间补丁」）。
