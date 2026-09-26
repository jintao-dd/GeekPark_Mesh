# Agent Quality · 任务级模型配置（冻结）

> Claim Support **v2.4c-2 frozen** · 无动态 router · Gold/Contract/Ranking 不变

**status=`frozen`**

## 任务 → 模型（固定配置表）

| 任务 | 模型 |
|------|------|
| Claim semantic judge | `claude-sonnet-4-6` |
| Answer compose (default) | `claude-sonnet-4-6` |
| Answer compose (sensitive/external) | `anthropic/claude-4.8-opus` |
| Evidence label | `claim_support v2.4c-2 (frozen; model-light)` |
| Temporal | `residual; do not patch claim_support` |

## 配置对比

### `opus_all` — 全 Opus（对照）

- semantic=`anthropic/claude-4.8-opus` · answer=`anthropic/claude-4.8-opus`
- quality_ok=`True` · wall≈317.74s
- Answer C/U: 1.0 / 1.0 (elapsed 139.46s / 24.23s)
- Evidence C/U: 1.0 / 1.0
- tokens: `{'prompt_tokens': 151060, 'completion_tokens': 17106, 'total_tokens': 168166, 'n_calls': 53}` · cost≈`0.50318`
- fails: `{'answer_canonical': [], 'evidence_canonical': [], 'answer_unseen': [], 'evidence_unseen': []}`

### `sonnet_all` — 全 Sonnet（默认候选）

- semantic=`claude-sonnet-4-6` · answer=`claude-sonnet-4-6`
- quality_ok=`True` · wall≈364.87s
- Answer C/U: 1.0 / 1.0 (elapsed 122.21s / 39.41s)
- Evidence C/U: 1.0 / 1.0
- tokens: `{'prompt_tokens': 146823, 'completion_tokens': 17790, 'total_tokens': 164613, 'n_calls': 54}` · cost≈`None`
- fails: `{'answer_canonical': [], 'evidence_canonical': [], 'answer_unseen': [], 'evidence_unseen': []}`

### `sonnet_sem_opus_ans` — semantic=Sonnet / answer=Opus（敏感成文）

- semantic=`claude-sonnet-4-6` · answer=`anthropic/claude-4.8-opus`
- quality_ok=`True` · wall≈376.11s
- Answer C/U: 1.0 / 1.0 (elapsed 143.05s / 29.45s)
- Evidence C/U: 1.0 / 1.0
- tokens: `{'prompt_tokens': 148724, 'completion_tokens': 17885, 'total_tokens': 166609, 'n_calls': 53}` · cost≈`0.521487`
- fails: `{'answer_canonical': [], 'evidence_canonical': [], 'answer_unseen': [], 'evidence_unseen': []}`

## Temporal residual（单列）

- pass_rate=`0.9167` · n_fail=`2`
- **禁止**为追 Temporal 1.0 修改已冻结 Claim Support

## 下一步

进入 Agent / Tool 设计；用 frozen Gold / Contract / Claim Support 验收 Agent。
