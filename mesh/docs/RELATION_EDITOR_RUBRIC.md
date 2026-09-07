# 关系编辑质量 Rubric（人审 + 回归）

用于审校关系卡与维护 `eval/relation_gold_v1.jsonl`。**不是** Ask gold recall。

## 通过条件（须同时满足）

1. **双端证据**：实线团队均有 `evidence.item_id` 指向该团队条目；虚线「→」不要求对方证据。
2. **论点可核对**：`body` / `details` 能从 evidence 文本中找到支撑，不能只靠标题词面碰运气。
3. **不假跨团队**：不得把同一 `(source_id, pointer)` 桶冒充两团队各有一手。
4. **标签诚实**：`decision_tier` 只影响排序；没有证据的卡应被隐藏而非强上。
5. **延续可标注**：若与上期标题/团队指纹相同，草稿候选应有 `continued_from`（有则加分，无则不否决）。

## 否决（任一即不通过）

- 无 evidence 的强关系卡进入读者可见切片  
- 团队徽章与 evidence owner 不一致  
- 正文无法由证据证明（空话 / 编造）  
- 把外部媒体单侧观察写成双边已联动  

## 维护

- Gold 样例放在 `eval/relation_gold_v1.jsonl`（每行一案）  
- 改 gate / writer 后跑：`pytest tests/test_relation_gold_smoke.py`
