# tmesh 2026-8-17 全量扫描报告

- **时间**：2026-08-31
- **范围**：published_json · 38 relations · items 语料
- **结果**：**38/38 通过** · blocked 假句未泄漏

---

## 扫描项

| 检查 | 结果 |
|------|------|
| 每条 relation 均有 evidence | ✅ 38/38 |
| evidence snippet ⊆ item 原文 | ✅ 0 失败 |
| evidence.team = item.owner_team | ✅ 0 不匹配 |
| evidence.source_id = item.source_id | ✅ 0 不一致 |
| blocked item 进入 published evidence | ✅ **0 泄漏** |
| detail「X 团队记录：」无对应 team item | ✅ 0 |
| relation.sources 无 evidence 支撑 | ✅ 0 |
| relation.teams 无 evidence 支撑 | ✅ 0 |
| 已知假句模式（硅谷BD+詹杨帆终端等） | ✅ published 中 **0 命中** |

---

## 已隔离的假数据（库内 blocked，未上线）

| item | 误标来源 | 内容摘要 | 状态 |
|------|----------|----------|------|
| **686** | 硅谷 BD…与面壁智能**詹杨帆**的对话 | 「詹杨帆认为终端是 AI 进入物理世界…」 | `blocked=1`，**未进 published** |
| **687** | 同上 | 「赛力斯与字节…iPhone 时刻」 | `blocked=1`，**未进 published** |

---

## 面壁智能卡（你关心的那条）

**detail：**「硅谷 BD 团队记录：面壁端侧模型已适配高通…」

| 维度 | 结论 |
|------|------|
| **真假** | ✅  grounded → **item #688**（硅谷 BD，`blocked=0`） |
| **部门** | ✅ detail 声称硅谷 BD ↔ evidence item owner_team = 硅谷 BD 团队 |
| **来源** | ✅ `硅谷 BD 团队建联记录 · 与面壁智能的对话`（**无詹杨帆**）→ source #23 |
| **詹杨帆相关内容** | 仅出现在 **编辑部** evidence（item 673–676，`编辑部沟通记录 · 与面壁智能詹杨帆的对话`） |

---

## lead / keywords

- lead：**不含**詹杨帆 / 物理世界假句
- keywords：**无**面壁/詹杨帆条目残留

---

## 来源标签频次（relations 内）

| 次数 | 来源 |
|------|------|
| 16 | 视频号周数据 |
| 11 | 商业化团队例会提及 |
| 8 | 社群例会提及 |
| 5 | 硅谷 BD 团队建联记录 · **与面壁智能的对话** |
| 5 | 编辑部沟通记录 · **与面壁智能詹杨帆的对话** |
| … | （完整列表见 scan 脚本输出） |

---

## 结论

当前 tmesh **published 读者页**上：

1. **没有**之前 prod 那种「硅谷 BD 捏造詹杨帆」假句泄漏
2. **部门归属**与 evidence item 一致
3. **来源**与 item.source_label 可追溯
4. 假 item 686/687 仍在库中但已 blocked，仅供审计，不影响页面

脚本：`deploy/scan_published_integrity.py` · 原始 JSON：`eval/reports/scan_2026-8-17.json`
