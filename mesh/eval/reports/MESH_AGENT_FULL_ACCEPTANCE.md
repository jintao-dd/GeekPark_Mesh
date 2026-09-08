# Mesh + Agent 全量验收报告

> 更新：2026-09-08  
> **总 verdict：🟢 Mesh + Agent v1 = GO** — Layer1–3 ✅；**Final Preview E2E ✅**；⑧ 飞书可开（仅接线）  
> 质量线（Temporal → Recall → Ranking）**不挡** v1 GO，GO 后另开。

| 层 | 状态 |
|----|------|
| Layer 1 契约 | ✅ |
| Layer 2 tmesh + LLM E2E | ✅ 10/10 |
| Layer 3 性能 | ✅ tmesh（见下） |
| Layer 3 故障 | ✅ 8/8 |
| Layer 3 并发隔离 | ✅ 6/6 + scope 不相交 |
| **Final Preview E2E** | ✅ `2026-09-08` |
| Layer 4 飞书 | ⬜ 可开（未跑） |

---

## 路线（锁定）

```
Final Preview E2E ✅ → Mesh+Agent v1 GO → ⑧ 飞书 MVP
                         ↓（之后）
              Agent Quality v1: Temporal → Recall → Ranking → …
```

Final 只验：Publish 前不可见 / Publish+Index 后可见 / Evidence / IssueRef / 不串期 / Draft 不泄漏。  
**不**把 Temporal Grounding 插进本次验收。

---

## Final Preview E2E ✅（tmesh · `2026-09-08`）

报告：`FINAL_PREVIEW_E2E.tmesh.json`  
脚本：`eval/run_final_preview_e2e.py`

真实链路：15 段聚合 Source → Pipeline（117 items）→ Preview（gate 通过）→ Publish → Index → Ask/Agent。

| 检查 | 结果 |
|------|------|
| Publish 前 Agent 不可见 | ✅ FTS=0；「当前没有可引用的已上线期次」 |
| Publish + Index 后可见 | ✅ marker + Evidence `ev:ctx:2026-09-08:1` |
| IssueRef | ✅ `explicit` → `2026-09-08` |
| 不串期（问 `2026-8-17`） | ✅ 唯一钉记不可见 |
| Draft / Raw 不泄漏 | ✅ `refuse`，无 data tools |
| publish_blockers | ✅ 空 |

墙钟大致：Pipeline ~2min；Preview ~2min；Agent 边界段 ~1min。  
本期 Preview 关系卡数为 0（gate 仍通过）；不挡生命周期门禁。

---

## Layer 3.1 性能（tmesh，n=5，`MESH_AGENT_USE_LLM=1`，issue=`2026-8-17`）

报告：`AGENT_PERF.tmesh.json`（容器内 `AGENT_PERF.json`）

| 指标 | P50 | P95 | P99 | max |
|------|-----|-----|-----|-----|
| **Agent total** | **14025 ms** | **17896 ms** | ~18097 | 18097 |
| **Ask total**（prepare+LLM） | **15103 ms** | **26109 ms** | ~28263 | 28263 |
| **Agent − Ask overhead** | **-2642 ms** | **3194 ms** | — | — |
| Tool（含检索+LLM） | ~12571–34629（单轮波动） | | | |
| Identity / Permission / Context / Intent | ≪50 ms（可忽略） | | | |

解读：
- Agent 额外编排开销相对 LLM **可忽略**；总耗时由 **Tool 内检索 + LLM 成文**主导。
- overhead 中位数为负：同题下 Agent 路径偶发快于「Ask prepare + 独立 LLM」对照（样本小、LLM 抖动大）；**P95 overhead ≈ +3.2s** 可作为保守上界。

---

## Layer 3.2 故障 ✅ 8/8

报告：`AGENT_FAULT.tmesh.json`

| 用例 | 结果 |
|------|------|
| LLM timeout → 不假 `llm_used` | PASS |
| LLM 空返回 → 不假 `llm_used` | PASS |
| Issue 不存在 → IssueRef none | PASS |
| Identity conflict → 无数据 Tool | PASS |
| unlinked refuse | PASS |
| draft refuse 无泄漏 | PASS |
| 空 Tool 不二次调用 | PASS |
| Tool 拒绝 draft surface | PASS |

---

## Layer 3.3 并发 ✅

报告：`AGENT_CONCURRENCY.tmesh.json`

- A：primary=商业化，chat=编辑部 → focus=**编辑部**
- B：primary=视频号，chat=视频号 → focus=**视频号团队**
- 6/6 PASS；`scope_key` A/B **不相交**

---

## 下一步

1. **⑧ 飞书 MVP**（仅接线；不重开 ①–⑦）  
2. （GO 后质量线）Temporal Gold → Temporal Grounding → Recall / Ranking  
3. CRM 进 Agent = 质量线后续，不挡飞书接线  

## Go / No-Go

- [x] Layer 1–2  
- [x] Layer 3 Agent 性能/故障/并发  
- [x] Final Preview E2E  
- [x] → **v1 GO** → ⑧ 可开  
