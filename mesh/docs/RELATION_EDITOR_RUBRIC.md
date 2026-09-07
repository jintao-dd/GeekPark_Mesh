# 关系编辑质量 Rubric（人审 + 回归）

用于审校关系卡与维护 Relation Gold。**不是** Ask gold recall。

## 项目状态（2026-09-07）

**④ Relation Gold v2：✅ 完成**

- 16 条 Gold；统一 schema；已有 valid/invalid Claim 对抗样例  
- 已建 lexical/gate baseline（`line_grounded` ≠ `claim_verdict`）  
- 测试通过：`pytest tests/test_relation_gold_schema.py tests/test_relation_gold_smoke.py`  
- **未修改** Decision / Evidence Gate / Writer 语义  

**① Claim Check：🟡 A1.1 shadow（rule_v1.1）— 已知 4 误报已清，暂不 enforce**

- A0/A1：见前；A1 No-Go 后做 A1.1 两处最小修复（CONTACT 缺词≠否定；blocker 非整卡下压）  
- 同 slug 复验：draft **0/10** invalid；published **1/17**（xAI parallel「建联」边界，非原 4 张）  
- Gold 10/10 仍绿；报告 `eval/reports/CLAIM_CHECK_A1_1_SHADOW_2026-8-17.md`  
- 下一刀：可选 A1.2（parallel 同构 CONTACT）→ 再谈 enforce  

## Gold 文件

| 文件 | 用途 |
|------|------|
| `eval/relation_gold_v2.jsonl` | **当前主基准**（统一 schema + claim 标签） |
| `eval/relation_gold_v1.jsonl` | 兼容保留（门禁/指纹 5 条） |
| `eval/reports/relation_gold_v2_baseline.json` | Claim 案例的 lexical/gate baseline（**≠** claim_valid） |
| `eval/relation_gold_lib.py` | 加载 / schema / baseline |

改 gate / writer / 未来 Claim Check 后跑：

```bash
pytest tests/test_relation_gold_schema.py tests/test_relation_gold_smoke.py
```

## Schema（v2）

顶层统一字段：`id` · `title` · `notes` · `expect` · `rel` · `items` · `claim`  
（指纹案：`rel`/`items`/`claim` 可为 null/`[]`；另需 `title_a/b` · `teams_a/b`）

`expect`：`keep` | `drop` | `team_via_evidence` | `fingerprint_stable` | `claim_valid` | `claim_invalid`

可选标注（本阶段可不被代码消费）：`expect_type` · `expect_tier` · `source`

`claim`（claim_* 必填）：`{"valid": bool, "reason": "..."}`，须与 `expect` 一致。

## 通过条件（须同时满足）

1. **双端证据**：实线团队均有 `evidence.item_id` 指向该团队条目；虚线「→」不要求对方证据。
2. **论点可核对（Claim Validity）**：`body` / `details` 的核心论断须被 evidence 支持；不得只靠标题词面或实体共现。
3. **不假跨团队**：不得把同一 `(source_id, pointer)` 桶冒充两团队各有一手；共现 ≠ 关系。
4. **标签诚实**：`decision_tier` 只影响排序；没有证据的卡应被隐藏而非强上。
5. **延续可标注**：若与上期标题/团队指纹相同，草稿候选应有 `continued_from`（有则加分，无则不否决）。

## 否决（任一即不通过）

- 无 evidence 的强关系卡进入读者可见切片  
- 团队徽章与 evidence owner 不一致  
- 正文无法由证据证明（空话 / 编造 / **说过头**：讨论≠合作、商量中≠已上线）  
- 把外部媒体单侧观察写成双边已联动  

## Claim vs lexical grounding

当前代码 `line_grounded` 是 **token overlap**，只能说明「词面能对上 evidence」。  
**不能**把 `current_line_grounded=true` 解释成 `gold_claim_valid=true`。  
Gold 里 `claim_invalid` 且 lexical 仍通过的案例，正是后续 Claim Check 要吃掉的缺口。
