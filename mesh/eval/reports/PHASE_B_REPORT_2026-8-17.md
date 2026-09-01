# Phase B 全量修复验收报告 · `2026-8-17`

**环境：** tmesh（`104.250.53.182` / `geekpark-tmesh`）  
**时间：** 2026-09-01  
**未动生产：** prod 未部署、未 publish

---

## 一、本次修复项（Phase B）

| 问题 | 修复 |
|------|------|
| 虚线卡被 verify 全部标 weak | 拆分 `weak`（编辑虚线）与 `needs_review`（叙事待核对） |
| 虚线卡无 evidence 被 drop | merge 保留 editorial weak / `→团队` 建议卡 |
| KPI「14」与下方卡数不符 | `sync_kpis_from_data()` 在 verify 后对齐 |
| preview 只写 draft，读者页不更新 | preview 完成同步 `published_json` + reindex |
| publish 误拦所有 weak | gate 只拦 `needs_review` + 强关系缺 evidence |
| title 误挂（单 entity「豆包」） | strict 优先，loose 需 ≥2 entity 交集 |

**改动文件：** `relation_candidates.py` · `relation_verify.py` · `relation_gate.py` · `issue_verify.py` · `preview_job.py` · `main.py`

**本地测试：** relation / KPI / evidence_loop 相关 **16 passed**

---

## 二、Before → After（读者页 published_json）

| 指标 | Before（Phase B 前） | After（preview 重跑后） |
|------|---------------------|------------------------|
| draft = published | ✗ 不同步 | **✓ 已同步** |
| KPI 可同步的关系 | **14** | **9** |
| 实际关系卡数 | 11 | **9** |
| KPI = 卡数 | ✗ | **✓** |
| 虚线 weak | 10 / 11（91%） | **1 / 9** |
| needs_review | 0 | **8**（叙事待 owner 核对） |
| 实线 strong | 1 | **8** |
| Label 种类 | 5（6 张「各知一半」） | **6 种**（各知一半仅 2 张） |
| team mismatch | 5 | **1** |
| orphan sources | 8 | **1** |
| strong 无 evidence | 0 | **0** |

---

## 三、After 关系卡一览（9 张）

| 标题 | Label | 样式 | evidence |
|------|-------|------|----------|
| 具身智能 · 世界机器人大会 | 同一件事，两个部门各知一半 | 实线 ✓ | 7 |
| 千问 · 智能体身份管理体系 | 采访对象也是客户 | 实线 · 待核对 | 3 |
| Agent 壁垒 · Evolvent AI | 两个部门各有判断 | 实线 · 待核对 | 2 |
| 刘靖康 · Insta360 影石 | 采访对象也是客户 | 实线 · 待核对 | 3 |
| 张鹏 · AGI Playground Singapore | 同一公司，不同触点 | 实线 · 待核对 | 4 |
| MiniMax · AGI Playground 2026 | 同一件事，两个部门各知一半 | 实线 · 待核对 | 2 |
| 支付宝 · HarmonyOS 7 | 一方报道了，另一方在接触 | 实线 · 待核对 | 2 |
| 飞书 · WorkBuddy | 一方报道了，另一方在接触 | 实线 · 待核对 | 2 |
| （第 9 张） | 海外接触，国内可能承接 | **虚线 1 张** | — |

读者页 `/2026-8-17` 已直接展示最新结果，**无需 `?preview=1`**。

---

## 四、Integrity & 发布闸门

**强关系 integrity**
- strong 无 evidence：**0** ✓
- duplicate titles：**0** ✓
- blocked leaks：**0** ✓
- team mismatch：**1**（待下轮 narrative/align 精修）
- orphan sources：**1**

**发布闸门（EDM / version）**
- 关系：**8 条叙事待核对** — 须在控制台确认或删改（`needs_review`，非虚线 weak）
- 其他：keywords/plans/contacts 有 unsupported 删减提示（Verify v2 正常行为）
- **虚线 weak 卡不再单独拦截 publish**

---

## 五、候选覆盖

- 代码候选：**19** 组跨团队共现
- LLM 本轮精选：**9** 张（质量优先，非全量填充）
- 未选用示例：豆包·字节跳动、视频号·小红书、OpenAI·Chrome 等（可在下轮 prompt 微调或 owner 手工补卡）

---

## 六、运行记录

1. Phase B 代码部署 tmesh + 容器重启  
2. Preview 第 1 次：LLM JSON 解析失败（已 retry）  
3. Preview 第 2 次：**成功**，message=`完成，读者页已更新`  
4. 验收脚本：`deploy/run_phase_b_acceptance.py`

**报告文件**
- `mesh/eval/reports/phase_b_before_2026-8-17.json`
- `mesh/eval/reports/phase_b_2026-8-17.json`
- `mesh/eval/reports/phase_b_2026-8-17.md`

---

## 七、结论 & 建议下一步

**已达成**
- ✓ 生成即展示（published 同步）
- ✓ KPI 与卡数一致
- ✓ 虚线 / 实线 / 待核对 语义分离
- ✓ Label 多样化，非清一色「各知一半」
- ✓ 强关系均有 evidence

**待 owner 动作（tmesh）**
1. 控制台核对 8 条 `needs_review` 关系叙事 → 确认或删改  
2. 视需要补回 LLM 未选的候选（如豆包、小红书等）  
3. tmesh 签 off 后再考虑 prod 同步

**可选技术跟进**
- 降低 narrative verify 误报率（减少 needs_review 数量）  
- LLM draft JSON 解析 retry（避免首次 preview 失败）
