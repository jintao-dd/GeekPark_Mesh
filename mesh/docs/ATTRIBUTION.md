# Attribution 归属语义（Audit）

## 问题背景

T13（内容中心·数据聚合）是团队归属错误的核心风险点。旧版 `resolve_item_owner()` 虽声明「用户选定 team 优先」，但存在两个绕过点：

1. **占位团队 + LLM fallback**：`source.team = 内容中心·数据聚合` 时，LLM 标注的 `owner_team` 会直接成为正式归属（面壁 686/687 案）。
2. **segment 覆盖 manual**：`segment_team` 优先级高于 `sources.team`，段推断可覆盖上传页人工选择。

## 层级语义

| 层级 | 字段 | 含义 |
|------|------|------|
| Source（容器） | `sources.team` | 上传页选项；T13 聚合包可为 `内容中心·数据聚合` |
| Item（事实） | `items.owner_team` | 正式归属，必须有 provenance |
| Evidence | `evidence.team` | **强制继承** `item.owner_team` |
| Narrative | `source_label` | 展示用，**永不参与** owner 判定 |

**T13 容器 vs 条目归属**：T13 上传选「内容中心·数据聚合」时，`sources.team` 保持占位/容器语义；**条目**不得把「内容聚合」当作 `owner_team`，应通过 `segment_rule` 或 `manual` 落到真实团队。

## Provenance 枚举

| 值 | 含义 | 能否写 owner_team |
|----|------|-------------------|
| `manual` | 上传页非占位 team | ✅ 最高权威 |
| `segment_rule` | T13 确定性段归属（章节标题 / `_classify`） | ✅ 次之 |
| `source` | `sources.team` 映射的非占位 owner | ✅ 兜底 |
| `llm_hint` | LLM 推断，仅 hint | ❌ 只写 `llm_owner_team_hint` |
| `unknown` | 无法确定 | ❌ block + needs_review |

## 硬规则

1. **人工指定 team 最高** — 上传页选「编辑部」时，segment / LLM 不得覆盖。
2. **占位团队不作正式 owner** — `内容中心·数据聚合`、`其他` 等。
3. **LLM 只产 hint** — `extract_items()` 写 `llm_owner_team_hint`，不写 `owner_team`。
4. **不确定则 block** — 宁可 `unknown/needs_review/block`，不猜测。
5. **source_label 不参与判定** — 仅 narrative / UI。
6. **evidence.team = item.owner_team** — `relation_candidates._build_evidence()` 强制继承。

## 代码路径

```
upload/paste (main.py)
  └─ is_mixed_source → split_bundle_ex → sources_from_split(upload_team=...)
       └─ 非占位 upload_team → 各段 sources.team = 用户选项（segment 推断仅入 meta）

extract (pipeline / llm.extract_source)
  └─ extract_items → llm_owner_team_hint（非 owner_team）
  └─ _segment_team ← seg.owner_hint / seg.team
  └─ apply_item_owner_guards（同 pointer 冲突）

persist (pipeline / main re-extract)
  └─ attribution.resolve_attribution / apply_attribution_to_item
       └─ items.owner_team + owner_provenance + llm_owner_team_hint
```

## sources_from_split 审计

**旧行为**：每段 `team = seg.owner_hint or seg.team`，完全忽略上传页 `use_team`。

**新行为**：若 `upload_team` 为非占位选项，各段 `sources.team = upload_team`，段推断团队写入 `meta.split.segment_inferred_team` 供审计；抽取仍用段 `stype` 选 prompt。

## 回归用例

- 用户选「编辑部」，T13 预拆段推断「硅谷 BD」→ `owner_team=编辑部`，`provenance=manual`。
- 用户选「内容中心·数据聚合」，无 manual → `segment_rule` 按段归属。
- LLM 标「编辑部」、无 manual/segment → `owner_team=NULL`，`blocked=1`，hint 落库。

## 与下游链关系

Attribution 锁死后，以下层才建立在可靠事实上：

**Entity → Relation → Evidence → Narrative → Verify → Owner**

本步 **不改 Agent / Planner / ReAct**。

## Publish 前 Verify（`app/attribution_verify.py`）

Attribution 与 Narrative **分开扫描**：

| 类别 | 行为 |
|------|------|
| **Attribution blockers** | 拦截 publish（硬约束 1–8） |
| **Narrative flags** | 仅记录 `source_label` 叙事 ≠ `owner_team`，**不**回头改 Attribution |

接入点：`main.publish_blockers()` → `attribution_publish_blockers()`。

扫描脚本：`deploy/run_attribution_verify.py --slug 2026-8-17`
