# Mesh + Agent 全量验收（当前主任务）

> **状态（2026-09-08）**  
> Layer 1–3 ✅ · **Final Preview E2E ✅**  
> **Mesh + Agent v1 = GO**  
> 下一步：⑧ 飞书 MVP（仅接线）  
> 质量线（Temporal → Recall → Ranking）GO 后另开，不回改 ①–⑦

报告：`eval/reports/MESH_AGENT_FULL_ACCEPTANCE.md`  
Final 报告：`eval/reports/FINAL_PREVIEW_E2E.tmesh.json`

---

## 两条线（必须分开）

```
主线（v1 收口）
  Final Preview E2E ✅ → Mesh+Agent v1 GO → ⑧ 飞书 MVP → 真实使用

质量线（GO 之后，Agent Quality / Retrieval Quality v1）
  Temporal Grounding → Recall → Ranking → Evidence → Answer Quality
  → CRM 等第二数据域 → Agent v2
```

**现在发现质量问题 ≠ 现在改架构。**  
①–⑦ 冻结不动。Temporal / Recall / Ranking **不插进** v1 验收回改。

---

## 四层门禁

```
Layer 1  契约                         ✅
Layer 2  tmesh 生命周期 + LLM         ✅
Layer 3  性能 → 故障 → 并发           ✅
Final    Preview 全链路 E2E           ✅ 2026-09-08
Layer 4  飞书 E2E                     ⬜ 可开
```

### Final Preview E2E（已过）

tmesh Issue `2026-09-08`：

`Source → Pipeline → Preview → Publish → Index → Ask → Agent`

| 检查 | 结果 |
|------|------|
| Publish 前 | Agent **看不到** ✅ |
| Publish 后 + Index | Agent **能看到** + Evidence ✅ |
| IssueRef | 正确 ✅ |
| 不串旧期 | ✅（唯一钉记） |
| Draft / Raw | 不泄漏 ✅ |

### 不做（现在仍不做架构回改）

- ❌ 重开 Agent / Context / Tool / Organization…  
- ❌ Temporal 进 v1 门禁 / 重写 RAG  
- ❌ CRM 接 Agent、ReAct、Planner、新 Tool  

Ask「最近/本周」措辞止血可保留；**正式 Temporal Grounding = GO 后第一刀**（先 Temporal Gold 20–30 题，再改检索）。

---

## GO 之后：Temporal Grounding（备忘）

IssueRef ≠ TimeWindow；优先 Event Time；无 `event_time` → `exact|range|unknown`（unknown 禁止说「最近发生」）。  
Gold 须带 `time_semantics` + `issue_scope`。排在 Recall/Ranking **之前**。
