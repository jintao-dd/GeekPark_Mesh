# Publish Report · 2026-8-17

**Closeout 完成 · 2026-08-31T16:53:13**

## 执行摘要

| 步骤 | 结果 |
|------|------|
| 低置信拆段确认 | source **#24** 已确认（`needs_review=false`，首轮 closeout 16:45） |
| source_label strip_prefix | **11** 条（含 `会议记录` 后缀扩展 + #1170 硅谷 BD 前缀剥离） |
| detail neutralize | **7** 行 / **6** 关系（detail↔evidence 索引对齐后中性化） |
| 全量 Verify | `lead_ok=true`，trim 0 |
| Attribution blockers | **0** |
| Relation narrative audit | **11 → 0** |
| Publish Gate | **可进入 Owner 确认上线** |
| owner_team / owner_provenance | **未修改** |

## Detail neutralize 样例

- `具身智能 · 世界机器人大会`：`编辑部记录：` / `视频号团队记录：` → `记录：…`
- `豆包 · 字节跳动` / `字节跳动 · 豆包` / `MiniMax · AGI Playground 2026` 等 6 关系共 7 行

## 修复说明（本轮）

1. **`_owner_for_detail`**：改为 detail 行号 ↔ evidence 同 index 对齐，不再全局搜「团队名匹配」导致误 keep
2. **`narrative_team_from_label`**：`外部媒体 · …` 视为来源类型描述，不计入 narrative mismatch
3. **`会议记录`** 纳入归属式后缀，可 strip 错误团队前缀

## Narrative flags 剩余

**19** 条（`needs_review` 类 + 正文内嵌团队名，按规则不自动改）

## Verify meta

```json
{
  "lead_ok": true,
  "keywords_rows_trimmed": 0,
  "plans_rows_trimmed": 0,
  "contacts_rows_trimmed": 0,
  "views_trimmed": 0,
  "corpus_chars": 31854
}
```

## 报告文件

- JSON：`mesh/eval/reports/PUBLISH_REPORT_2026817.json`
- tmesh 容器内：`/srv/mesh/eval/reports/PUBLISH_REPORT_2026817.{json,md}`
