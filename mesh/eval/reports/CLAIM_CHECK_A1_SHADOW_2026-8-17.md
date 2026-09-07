# Claim Check A1 Shadow 对账（tmesh `2026-8-17`）

> 模式：`MESH_CLAIM_CHECK_MODE=shadow`（未 enforce）  
> 规则：`rule_v1`（**未扩规则**）  
> 脚本：`deploy/_audit_claim_shadow.py`  
> 原始 JSON：`claim_shadow_tmesh_2026-8-17_{draft,published}.json`

## 分布

| Lane | n | valid | invalid | uncertain | invalid_ratio | by_reason |
|------|---|-------|---------|-----------|---------------|-----------|
| draft_json | 10 | 9 | 1 | 0 | 10% | ok:9, status_upgrade:1 |
| published_json | 17 | 14 | 3 | 0 | 17.6% | ok:14, status_upgrade:2, evidence_blocker:1 |

未出现「30 张里 12 张 invalid」级爆炸；比例低于 warn 线 25%。

## ① 漏网（有证据 + lexical 过 + 说过头）

本期 **published/draft 上未见典型 g09/g12 级过头合作卡被 shadow 打掉**——现网这期读者卡里，过头合作表述本身较少；Gold 对抗案仍由单测 10/10 覆盖。

lexical 仍过但 claim=invalid 的卡，本期更像 **误报**（见下），不是成功抓漏网。

## ② 误杀（重点）

### published ×3（建议判为误报）

1. **秒动科技杨硕…**（`evidence_blocker`）  
   - detail/body 写「计划两周后参加选题会」——证据里**同样有这段计划**，另有「尚无反馈」。  
   - 规则用「尚无反馈」把 evidence 上限压到 3，导致同级「计划」(5) 被打。  
   - **人审**：不应 invalid（计划表述与证据一致）。

2. **Helloboss CEO…编辑部接触**（`status_upgrade`，watch/one_sided）  
   - body「编辑部接触…」；evidence 是人物档案，**未出现「接触」字面** → evidence_strength=0。  
   - **人审**：典型单侧建联卡，应 valid（类 Gold g08）。

3. **韩国…海外接触**（同上）  
   - 同构：标题/body 用「接触」，snippet 无该词。  
   - **人审**：应 valid。

### draft ×1

4. **擎羽科技：编辑部接触获早期赛道信号**（watch）  
   - title「接触」> evidence 字面强度。同构误报。

### 正常弱关系未大面积误杀

`info_complement` / `parallel_tracks` / 多数 `watch` 在 `valid_watch_parallel_sample` 中保持 valid（Founder Park、豆包平行、百度 Workshop 选题等）。

## ③ Go / No-Go

| 条件 | 结果 |
|------|------|
| Gold claim 10/10 | ✅（本地 pytest） |
| 真实一期 invalid 基本符合人审 | ❌ 本期 3–4 张 invalid **多为误报** |
| valid 无明显误杀（平行/观察） | 🟡 大体 OK，但「接触」字面差导致 watch 误杀 |
| keep/drop/team/fingerprint smoke | ✅ 未改 |
| shadow 可审计 | ✅ |

**结论：A1 = No-Go enforce。**  
保持 `shadow`。下一步应是 **极小范围修 rule_v1 两处已知误报**（不是扩规则库），再重跑同一期 shadow：

1. `watch` / `one_sided`：有 evidence 时，「接触/建联」类 CONTACT 不因 snippet 缺同词而判定 upgrade。  
2. blocker：证据中同时存在「计划」与「尚无反馈」时，不要用 blocker 压掉与证据同级的「计划」表述。

修完并 shadow 复验通过后，再开 `MESH_CLAIM_CHECK_MODE=enforce`。
