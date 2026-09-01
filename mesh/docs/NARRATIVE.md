# Narrative 展示层规范化

**原则：`item.owner_team` 是唯一归属事实来源。Narrative 清理只改展示字段，绝不反推或修改 owner。**

```
owner_team          ← 唯一事实（Attribution 已锁死）
source_label/detail ← 只能描述来源/内容，不得重新推断团队
```

## 硬规则

| 情况 | 处理 |
|------|------|
| `source_label` 带**错误团队前缀**（归属式前缀） | 删除/替换团队前缀，保留业务语义 |
| label 与 `item.owner_team` 不一致 | 以 `owner_team` 为准做**展示清理**，不改 owner |
| label 本身有真实业务含义 | 保留业务语义，不强行删除 |
| LLM narrative 猜出的团队 | 不得升级成 owner |
| `item.owner_team` | **绝不由 Narrative 反推修改** |
| 无法判断怎么改 | `needs_review`，不要自动猜 |

## 不做全局字符串替换

「硅谷 BD」在 label 中可能是：

1. **错误的归属前缀** — `硅谷 BD 团队建联记录 · …`（应剥离前缀）
2. **被讨论的业务对象** — `与硅谷 BD 团队合作的方案`（应保留）

因此 cleaner 判断的是 **`[团队前缀] + [归属式业务后缀]`**，而不是「发现团队名 ≠ owner → 删掉所有团队名」。

## 归属式前缀（可剥离）

仅当 label **以正式团队名开头**，且后续匹配归属式后缀时，才视为错误前缀：

- `建联记录` / `沟通记录` / `团队建联` / `见人记录` / `例会` / `周报` / `数据` / `妙记` …
- `团队 · …` / ` · …`（段标题式）

## 示例

| owner_team | 原 source_label | 清理后 | 说明 |
|------------|-----------------|--------|------|
| 编辑部 | 硅谷 BD 团队建联记录 · 与面壁… | 团队建联记录 · 与面壁… | 剥离错误归属前缀 |
| 编辑部 | 编辑部沟通记录 · 与千问… | （不变） | 前缀与 owner 一致 |
| 编辑部 | 讨论硅谷 BD 团队入场策略 | （不变，needs_review 可选） | 硅谷 BD 是内容对象 |
| 编辑部 | 外部媒体 · TechCrunch | （不变） | 来源类型描述，非 owner 宣称 |

## detail 行

`{团队}记录：…` 格式：

- 团队 ≠ 对应 evidence 的 `owner_team` → 改为中性 `记录：` 或 `{owner_team}记录：`（**仅展示**）
- 不修改 relation.teams / evidence.team（Evidence 层已锁死）

## 代码

- `app/narrative_clean.py` — `clean_source_label()` / `clean_detail_line()` / `audit_item_narrative()`
- **不**调用 `resolve_attribution()`，**不**写 `owner_team`
- Verify：`attribution_verify.narrative_flags` 继续只 flag；清理为独立可选步骤

## 流水线位置

```
Source → Attribution 🔒 → Entity/Relation → Evidence 🔒 → Narrative ← 当前 → Verify → Publish
```
