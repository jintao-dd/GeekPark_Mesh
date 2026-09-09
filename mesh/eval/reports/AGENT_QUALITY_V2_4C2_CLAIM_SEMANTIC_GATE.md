# Agent Quality v2.4c-2 · Claim Semantic Extension Gate — PASSED / FROZEN

> **gate_ok = true** · **worse = []** · Claim Support semantic extension **frozen**  
> 不再开 claim_support shadow 小版本；不为单个新 case 打补丁。

## Hard gates

| Gate | Result |
|------|--------|
| Canonical Answer 21 | **1.0** |
| Canonical Evidence 20 | **1.0** |
| Unseen Answer 7 | **1.0** |
| Unseen Evidence 8 | **1.0**（含 E28） |
| worse | **[]** |
| Ranking / Recall | ok（v1.4 / Vector OFF） |
| Temporal | 0.9167（T06/T08 wall-clock；**T19 本轮通过**，仍单列 residual 历史） |

## 生产接入

- `assess_claim_support()` = deterministic + selective semantic extension（默认 `MESH_CLAIM_SEMANTIC=1`）
- Kill switch：`MESH_CLAIM_SEMANTIC=0`
- 高风险 gate：无 det `direct_support_for_strong_claim` 时禁止 LLM 升 `supported`（防 false-support）
- 三维度：`claim_strength` / `evidence_entailment` / `counter_evidence`

## Freeze 清单

| Layer | Status |
|-------|--------|
| Ranking v1.4 | frozen |
| Retrieval Recall | frozen |
| Vector | OFF |
| **Claim Support + semantic ext** | **frozen (v2.4c-2)** |
| Agent Contract | frozen |
| Ontology schema | frozen（仅文档） |
| Wiki / ES / Graph / Agentic RAG | frozen |
| Gold | unchanged |

## Next（本轮立刻）

**Model A/B**（只换模型，不改规则/Prompt/Retrieval/Ranking/Gold）

产物：`eval/reports/baselines/QUALITY_V2_4C2_CLAIM_SEMANTIC_GATE.json`
