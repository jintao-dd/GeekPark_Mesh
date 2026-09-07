# Claim Check A1.2（rule_v1.2）

> 模式：shadow（**未 enforce**）  
> 仅放宽：CONTACT 缺词豁免语境 += `parallel_tracks` / `info_complement`  
> **未扩词表**；平行语境 DEAL+ 过头合作仍 invalid

## 代码

`_contact_soft_context` = watch/one_sided/overseas **或** parallel_tracks/info_complement  
条件不变：有 evidence + claim≤CONTACT + 无接触否定 → 不因缺同词 upgrade

## Gold

25 passed（含 claim_* **10/10**）  
g09/g12/g13/g14 仍 invalid；g06–g08/g15 仍 valid；新增 xAI parallel「建联」单测 valid，parallel 统一合作仍 invalid。

## 同 slug `2026-8-17` published

| | A1.1 | A1.2 |
|--|------|------|
| n | 17 | 17 |
| invalid | 1（xAI 建联） | **0** |
| ratio | 5.9% | **0%** |

已知误报 + xAI 边界：**全部消失**。parallel/info_complement 样例在 valid 抽样中。

## 第二真实一期 `2026-08-21` published（防单期调参）

| n | valid | invalid | ratio |
|---|-------|---------|-------|
| 5 | 0 | 5 | 100% |

**重要**：这 5 张卡的 `evidence: []` 全空（legacy/弱关系稿），`evidence_strength=0`。  
判 invalid 是因为**无证据可支撑** CONTACT/沟通类陈述，**不是**「有 evidence 但缺同词」同构误杀。  
与 A1.2 放宽目标正交；空 evidence 卡本就不该进读者切片（现有 filter_ungrounded 也会挡）。

人审结论：第二期 **无「有证据却被 CONTACT 缺词误杀」**；全灭来自缺 evidence 结构，不否决 A1.2 规则本身。

## 验收清单

| 项 | 结果 |
|----|------|
| Gold 10/10 | ✅ |
| 已知误报 + xAI 消失 | ✅ |
| parallel/info 正常样例无误杀（8-17） | ✅ |
| g09/g12/g13/g14 仍 invalid | ✅ |
| published 8-17 invalid=0 | ✅ |
| 第二期 shadow | ✅ 已跑；无效卡均为 evidence 空 |

## Enforce

**仍为候选，本步不开闸。**

建议开闸条件（沿用你的最终标准）：

- Gold ✅  
- 2026-8-17 ✅（invalid=0）  
- 第二期：优先再找一期 **evidence 齐全** 的 published 做 shadow（或先把 08-21 空 evidence 卡当「应藏」人工确认）  
- 确认无 valid 误杀 + overclaim Gold 仍能打  

然后才：`MESH_CLAIM_CHECK_MODE=enforce`

原始 JSON：  
`claim_shadow_a12_tmesh_2026-8-17_published.json`  
`claim_shadow_a12_tmesh_2026-08-21_published.json`
