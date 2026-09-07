# Claim Check A1.1（rule_v1.1）同 slug 复验

> slug：tmesh `2026-8-17`  
> 模式：shadow（**未 enforce**）  
> 相对 A1：仅两处最小修复，未扩规则库

## 修复内容

1. **watch / one_sided CONTACT**：有 evidence 且无「未接触」类否定时，不因 snippet 缺「接触/建联」字面判 upgrade。  
2. **blocker**：取消整卡全局下压；任一条 evidence 支撑该强度即 OK；完成态（PUSH+）才与未完成标记冲突。

## Gold

`pytest tests/test_relation_claim_check.py …` → **23 passed**（含 claim_* **10/10**）。  
g09/g12/g13/g14 仍 invalid；g06/g07/g08/g15 仍 valid。

## 真实一期对比

| Lane | A1 invalid | A1.1 invalid | ratio |
|------|------------|--------------|-------|
| draft | 1（擎羽） | **0** | 0% |
| published | 3（秒动/Helloboss/韩国） | **1** | 5.9% |

### 已知 4 误报

| 卡 | A1 | A1.1 |
|----|----|------|
| 擎羽（draft）接触字面 | invalid | **valid** |
| 秒动 计划 + 尚无反馈 | invalid | **valid** |
| Helloboss 接触缺词 | invalid | **valid** |
| 韩国海外接触缺词 | invalid | **valid** |

### 残留 1 张（新边界，非原 4 张）

- **xAI：编辑部技术成员建联与视频号财报选题**（`parallel_tracks`）  
  - body「建联」= CONTACT(3)；evidence 为人名档案/财报选题，无建联字面 → status_upgrade  
  - **人审**：平行触点表述，宜 valid  
  - **不在 A1.1 两处修复范围内**（仅放行了 watch/one_sided）。记入 A1.2 候选：parallel 的 CONTACT/建联亦可「缺词≠否定」。

## A1.1 验收清单

| 项 | 结果 |
|----|------|
| Gold claim 10/10 | ✅ |
| g06/g07/g08/g15 不新增误杀 | ✅ |
| 已知 4 误报全部消失 | ✅ |
| 不出现新的明显 valid 误杀 | 🟡 剩 1 张 parallel/建联（轻） |
| relation smoke 全绿 | ✅ |
| shadow audit 字段结构不变 | ✅ |

## Go / No-Go enforce

**仍不建议立即 enforce。**  
已知误报已清，但 parallel「建联」同构边界还在。建议：

- 保持 `shadow`  
- 可选 **A1.2**：把「CONTACT 缺词 ≠ 否定」从 watch/one_sided **同逻辑**扩到 `parallel_tracks` / `info_complement`（仍不扩词表）  
- A1.2 后 published invalid→0 或仅真过头卡，再 `MESH_CLAIM_CHECK_MODE=enforce`

原始 JSON：`claim_shadow_a11_tmesh_2026-8-17_{draft,published}.json`
