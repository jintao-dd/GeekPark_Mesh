# Claim Check Final Shadow — tmesh `2026-8-17`

> 环境：tmesh（管理台显示 2026.9.7）  
> 模式：`MESH_CLAIM_CHECK_MODE=shadow`（本报告**未**改 enforce）  
> 规则：`rule_v1.2`  
> 日期：2026-09-07

## 为何选这期

tmesh 仅两期上线：`2026-8-17`（evidence-rich）与 `2026-08-21`（全空 evidence，已排除）。  
本期 published：**17/17 有 evidence，0 空**。

## 结果

| Lane | n | valid | invalid | uncertain | ratio |
|------|---|-------|---------|-----------|-------|
| published | 17 | **17** | **0** | 0 | 0% |
| draft | 10 | **10** | **0** | 0 | 0% |

`by_reason`：全部 `ok`。

valid 抽样类型覆盖：`info_complement` / `parallel_tracks` / `one_sided` / `overseas_link` — **无误杀迹象**。

盯过的过头路径（讨论→合作、计划→已完成、接触→达成、共现→联合、parallel→统一商务）在本期真实稿中未大量出现；能力仍由 Gold **g09/g12/g13/g14** 单测兜住（Final 同步回归 **29 passed**）。

## Go 清单

| 项 | 结果 |
|----|------|
| Gold claim 10/10 | ✅ |
| evidence-rich issue shadow | ✅ `2026-8-17` |
| 无明显 valid 误杀 | ✅ invalid=0 |
| relation / claim / gate boundary 回归 | ✅ 29 passed |
| Gate → Writer → Claim 边界测试 | ✅ |

## 判定

**Final Shadow：✅ 通过。**  
① Claim Check 具备开闸条件；开闸动作：

```text
MESH_CLAIM_CHECK_MODE=enforce
```

（容器/compose 环境变量，重启 web+worker 后生效；默认仍为 shadow，需显式设置。）

原始 JSON：  
`claim_shadow_final_tmesh_2026-8-17_published.json`  
`claim_shadow_final_tmesh_2026-8-17_draft.json`
