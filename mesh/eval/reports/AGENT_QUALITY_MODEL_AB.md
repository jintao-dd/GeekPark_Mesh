# Agent Quality · Model A/B

> 固定：Ranking v1.4 · Recall · **Claim semantic v2.4c-2 frozen** · Contract · Prompt · Gold  
> 只换 `MESH_LLM_MODEL`。tmesh · elapsed ≈ 819s

| | Model A | Model B |
|--|---------|---------|
| id | `anthropic/claude-4.8-opus` | `claude-sonnet-4-6` |
| Answer canonical | **1.0** (11/21 llm成文, 108.8s) | **1.0** (11/21 llm成文, 96.2s) |
| Evidence canonical | **1.0** | **1.0** |
| Answer unseen | **1.0**（semantic 拒答短路，llm_used=0） | **1.0** |
| Evidence unseen | **1.0** | **1.0** |
| Temporal | **0.9583**（1 fail，两侧相同） | **0.9583** |

false-support / over-abstain：本轮 Gold 硬门下两侧均为 0 失败。

## 决策（非动态 router）

**质量差距 ≈ 0；延迟 A 略慢。**  
在 **semantic extension 已冻结** 的前提下，**不必所有任务都绑 Opus**。

建议任务级分层（下一步落地，非本轮）：

| 任务 | 建议 |
|------|------|
| Claim semantic judge（高风险 gate） | 可先试 Sonnet；掉点再升 Opus |
| Answer 成文（非拒答） | 默认可 Sonnet；敏感/对外稿 Opus |
| Evidence label | 已由 semantic ext 主导，模型敏感度低 |

**不要**上动态 router；先按任务表切换模型配置验证。

产物：`eval/reports/baselines/QUALITY_MODEL_AB.json`
