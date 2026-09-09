# Agent Quality v2.3e · Claim strength + contradicted 反证对齐

> Ranking v1.4 / Recall / Vector / Contract / Wiki / ES / Agentic RAG / 模型路由：**全部冻结**。  
> 本轮目标：清 P0（A21/E16/E18），**不是**刷 pass_rate。主 Gold **尚未**合入 accept 集。

## 修复

### P0-1 A21 / E16（同刀）

`claim_support` 增加 **claim strength**：

- 强确定性 claim（已量产 / 量产上车 / 证明…量产 / 能否证明…量产 / 交割…）  
- **必须**有直接支持该结论的 evidence  
- topic/entity overlap → `insufficient`（不得 `supported`）  
- **不是**「含量产就一律 abstain」

### P0-2 E18（不放宽 Gold）

- 保持 `expect=contradicted`  
- 否认类：二次对齐 published 接触反证 → `contradicted`  
- 无明确反证 → `insufficient`  
- Answer 仍可对二者 abstain（**Evidence label ≠ Answer safety**）

## tmesh 扩展验收（accept Gold）

| 门 | 结果 |
|----|------|
| A21 | `supported` → **`insufficient`** ✅ |
| E16 | `supported` → **`insufficient`** ✅ |
| E18 | `insufficient` → **`contradicted`** ✅ |
| Answer accept pass | **1.0**（21/21） |
| Evidence accept pass | **1.0**（20/20） |
| A18/A19/A20 | 不回退 ✅ |
| E13/E14/E15/E17/E19/E20 | 不回退 ✅ |
| worse / 新 hard fail | **[]** |
| 新增 over-abstain（E20） | 无（仍 `supported`） |
| 新增 false-support | 无 |

软噪声：abstain 路径清空 refs → `hits_without_evidence_refs`（非产品回归）。

## 基线

| 字段 | 值 |
|------|-----|
| ranking_version | v1.4 |
| vector | OFF |
| MESH_AGENT_USE_LLM | 0 |
| model_version | anthropic/claude-4.8-opus（本轮未成文） |
| claim_support | v2.3e strength + denial counter-evidence |

## Ontology

仅文档补强 claim strength / contradicted / insufficient 语义；**不扩建**、不上 KG。

## 未做（按你的顺序）

- ❌ 尚未把 accept Gold 合入主 `*_v2.jsonl`（等你确认进入稳定性验证后再合）  
- ❌ 模型 A/B  
- ❌ Wiki / ES / Ranking

## 产物

- `app/agent/claim_support.py`  
- `eval/reports/ANSWER_EVIDENCE_ACCEPTANCE_latest.json`（tag accept_v23e）  
- Runner：`eval/run_answer_evidence_acceptance.py`
