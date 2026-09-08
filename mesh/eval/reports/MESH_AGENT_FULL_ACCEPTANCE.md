# Mesh + Agent 全量验收报告

> 开始：2026-09-08  
> 环境：本地优先；tmesh / prod 分栏填写  
> 门禁：⑦ 已 20/20（真实 Adapter）→ 本报告全部 PASS 后才开 ⑧

## 总判

| 项 | 状态 |
|----|------|
| ⑦ Agent v1 | ✅ 20/20（见 `AGENT_V1_CONTRACT_20260908.md`） |
| 全量验收 | 🟡 **进行中** |
| ⑧ 飞书 MVP | ⛔ 暂缓 |

**总 verdict：** 🟡 本地回归 6/6 PASS；全链路 / 性能 / 加压容错 / 并发仍 ⬜ → **未结案**

---

## 1. 全链路流程

| 步骤 | 本地 | tmesh | 备注 |
|------|------|-------|------|
| Source → Pipeline | ⬜ | ⬜ | |
| Preview | ⬜ | ⬜ | 只读页不得误触发 |
| Relation → Claim | ⬜ | ⬜ | |
| Publish → Index | ⬜ | ⬜ | |
| Ask | ⬜ | ⬜ | |
| Agent | ✅ 契约+真实数据 | ⬜ | Harness 已过 |

原则：异常路径见 §6；不得把失败包装成成功。

---

## 2. Agent 正确性

| 层 | 结果 | 证据 |
|----|------|------|
| Identity | ✅ | Agent 20/20 身份矩阵 |
| Permission | ✅ | conflict / unlinked deny |
| Context | ✅ | IssueRef / chat≠primary |
| Intent | ✅ | rule intent |
| Tool | ✅ | ≤1 + 自防御 |
| Evidence | ✅ | 真实 published/relation Evidence |
| Answer | ✅ | fingerprint ≠ trace |

---

## 3. 数据安全

| 项 | 本地 | 备注 |
|----|------|------|
| Draft/Raw/Unpublished | ✅（契约） | 无泄漏断言 |
| Issue bypass | ✅ | Tool deny |
| Team bypass | ✅ | Tool deny |
| Permission bypass | ✅ | ACL |
| Published-only | ✅ | |

远程加压 / 恶意 payload 复测：⬜

---

## 4. 回归

| 项 | 本地结果 | 时间 | 备注 |
|----|----------|------|------|
| Agent 20/20 | PASS (7.4s) | 2026-09-08T05:02Z | `test_agent_v1_contract.py` |
| Ask 25/25 golden | PASS (16.3s) | 2026-09-08T05:02Z | `run_final_eval.py --corpus golden` |
| Relation Gold | PASS (1.7s) | 2026-09-08T05:02Z | schema + smoke |
| Claim Check tests | PASS (2.9s) | 2026-09-08T05:02Z | claim_check + gate boundary |
| T13 | PASS (1.7s) | 2026-09-08T05:02Z | segment quality 5/5 |
| Publish / write boundary | PASS (8.6s) | 2026-09-08T05:02Z | publish + published write |

本地回归块：**6/6 PASS**（`eval/run_mesh_agent_full_acceptance.py --phase local_regression`）。  
仍缺：全链路手测 / tmesh Ask25 / perf / 加压容错 / 并发。

---

## 5. 性能（P50 / P95）

| 路径 | P50 | P95 | n | 环境 |
|------|-----|-----|---|------|
| Pipeline | | | | |
| Preview | | | | |
| Publish | | | | |
| Ask | | | | |
| Agent | | | | |
| Agent − Ask 额外延迟 | | | | |

⬜ 待测

---

## 6. 容错（重点：出错时不越权）

| 场景 | 期望 | 结果 |
|------|------|------|
| LLM timeout | 可见失败 / 降级；不伪造成功 | ⬜ |
| Tool timeout | 同上 | ⬜ |
| 空结果 | 不二次数据 Tool；不编造 | ✅（契约） |
| Issue 异常 / draft slug | IssueRef none / deny | ✅（契约） |
| Identity conflict | 无数据 Tool | ✅（契约） |
| Permission failure | deny | ✅（契约） |
| DB / Redis / queue 异常 | 失败可见 | ⬜ |

---

## 7. 并发

| 场景 | 结果 |
|------|------|
| 多人 Ask | ⬜ |
| 多人 Agent | ⬜ |
| Preview + Agent | ⬜ |
| LLM pool 满 | ⬜ |
| 同用户连续 | ⬜ |
| 不同用户 Context 隔离 | ✅ scope_key（契约） |

---

## 结论门槛

全部 7 块无 ⬜ 阻塞项，且回归不得退化 → **全量 PASS** → 方可进入 ⑧。
