# 关系叙事（第二阶段 · 仅叙事）

你会收到已通过 Evidence Gate 的 **locked_relations**。字段 `label`、`teams`、`sources`、`evidence` 已由系统锁定，**不得修改**。

## 只输出
对每个 locked relation（按 `candidate_id` 对齐）输出：
- `candidate_id`
- `title`（读者可见标题，可基于 candidate_title 微调，不得引入 items 外主体）
- `body`（一句话，须能被 evidence snippets 论证）
- `details`（各实线团队一条，格式「团队记录：…」，须来自 evidence，不得编造）

## 禁止
- 修改 label / teams / sources / evidence / weak / item_ids
- 编造 evidence 未覆盖的事实
- 新增或删除关系卡
