# Feishu Agent v1 · 产品硬边界（冻结）

> **质量基线：** Agent Quality v3.0 Production Baseline / E2E RC  
> **原则：** 只接线、不扩大脑；不回头开 v2.x / v3.x 质量微调。

## 主链路（不可改）

```
Feishu User
  → Agent / Ask（现有 handle_message）
  → context / follow-up（现有 context_refs 合同）
  → intent / entity（现有 rule_classify）
  → Retrieval + Ranking v1.4
  → Claim Support v2.4c-2
  → Evidence
  → Answer（默认 Sonnet；敏感/对外 Opus override）
```

## 产品范围（上限）

1. 事实查询  
2. Relation / Entity 查询  
3. Team / Attribution 查询  
4. Follow-up / 上下文追问  

回归门禁：v3.0 S01–S05 + Canonical Answer/Evidence + Claim Support + Temporal + Ranking smoke。

## 核心产品原则（硬）

**每个可验证事实回答，必须让用户看到对应 Evidence / 期次引用。**

- Evidence 必须与答案中的核心 claim 对齐（复用 Claim → EvidenceRef → Support）。  
- 禁止只展示「相关内容」却不支撑当前 claim。  
- abstain / insufficient / contradicted 须显式可见，并尽量带期次或判定理由。  
- Temporal：「最近」类问句沿用现有 caveat，不偷偷改规则。

## 模型策略

| 任务 | 模型 |
|------|------|
| semantic judge | `claude-sonnet-4-6` |
| answer（默认） | `claude-sonnet-4-6` |
| answer（敏感/对外） | `anthropic/claude-4.8-opus` |

环境变量覆盖允许；**禁止** Dynamic Router。

## 明确禁止

新 Planner · 新 Retrieval · Wiki · ES · Graph / Neo4j · Agentic RAG · Memory 架构 · 写回 · 自动扩展能力 · 大规模 Ontology 扩建

## 真相源

Published items / relations / Evidence only。LLM 摘要不得成为第二 truth source。

## 实现入口

- 编排：`app/agent/runtime.py` → `handle_message`  
- 飞书展示：`app/agent/feishu_reply.py` → `format_display_text`  
- HTTP harness：`POST /api/agent/v1/message`  
- 飞书事件：`POST /api/feishu/bot/event`（接线，不改大脑）
