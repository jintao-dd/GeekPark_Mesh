# Agent Quality v3.0 · Production Baseline / E2E RC

**status=`production_baseline`** · production_baseline=`True` · elapsed_s=74.76

## Baseline manifest（冻结零件）

- Ranking `v1.4` · Retrieval `lexical_fts_recall_frozen` · Vector `OFF`
- Claim Support `v2.4c-2_semantic_extension_frozen`
- Model policy: `{"default_semantic": "claude-sonnet-4-6", "default_answer": "claude-sonnet-4-6", "sensitive_answer_override": "anthropic/claude-4.8-opus", "dynamic_router": false}`
- Contract / Ontology / Prompt: frozen

## Frozen-layer regression

```json
{
  "temporal_pass_rate": 1.0,
  "temporal_fail_ids": [],
  "answer_canonical_pass_rate": 1.0,
  "answer_canonical_fail_ids": [],
  "answer_canonical_usage": {
    "prompt_tokens": 58697,
    "completion_tokens": 6768,
    "total_tokens": 65465,
    "n_calls": 18,
    "n_retries": 0
  },
  "answer_canonical_elapsed_s": 101.28,
  "evidence_canonical_pass_rate": 1.0,
  "evidence_canonical_fail_ids": []
}
```

## Temporal residual

```json
{
  "pass_rate": 1.0,
  "fail_ids": [],
  "known_residual": [
    "T08",
    "T06"
  ],
  "new_regressions": [],
  "note": "T06/T08 wall-clock known; T19 not forced blocker; do not patch claim_support"
}
```

## Scenario E2E

pass_rate=`1.0` · 5/5

| Scenario | Title | Pass | Fail layers |
|----------|-------|------|-------------|
| S01 | 事实查询：期次内人物动作 | ✅ | — |
| S02 | 关系/实体：主体相关联系 | ✅ | — |
| S03 | 强结论诱导：量产完成态 | ✅ | — |
| S04 | 组织归属：团队线索 | ✅ | — |
| S05 | 追问：上下文继承 | ✅ | — |

## Gates

- ✅ `canonical_answer_no_regression`
- ✅ `canonical_evidence_no_regression`
- ✅ `temporal_no_new_regression`
- ✅ `scenarios_all_pass`
- ✅ `no_systemic_layer_blocker`
- ✅ `no_false_support_in_scenarios`

## 意义

v3.0 证明冻结组件可组成稳定、可解释、可回归的完整 Agent 基线。
下一阶段：Feishu Agent 产品化（不回头开 v2.x 微调）。
