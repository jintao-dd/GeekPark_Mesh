# Ask Analysis 接口契约（锁定）

> 版本：`1.0` · 适用范围：`POST /api/ask`、`POST /api/ask/stream`  
> 变更规则：新增字段可向后兼容；删除/改语义必须升 major。

## 1. 目标流水线

```
用户问题
  → Context Router（independent | followup）
  → Retrieve → Group → Source(≤4 并行) → Cross? → Verify
  → token* → done
```

硬性规则：

| # | 规则 |
|---|------|
| ① | 每问独立 `analysis_id` |
| ② | `independent` 不继承任何会话上下文 |
| ③ | `followup` 只继承上一 analysis 的结构化 `context_refs` |
| ④ | Cross 只能使用**当前** `analysis_id` 的 Source 结果 |
| ⑤ | Claim 必须能挂到本轮 Evidence（Verify 强制） |
| ⑥ | 历史 AI Answer / NL 分析结果 **不得** 作为证据或成文上下文 |
| ⑦ | SSE 断开 ≠ Task 取消（目标态；见审计「未完成」） |
| ⑧ | 每个 `step` 必须带 `status`: `running` \| `completed` \| `skipped` \| `error` \| `timeout` |

`chunk_ids` 在 `context_refs` 中仅作**检索定位种子**（问句 hint / soft boost），不得直接当作本轮答案证据。

## 2. SSE 事件集（仅此集合）

| type | 何时 | 必填字段 |
|------|------|----------|
| `meta` | 检索准备完成后、分析步骤前 | `mode`, `n_context`, `session_id`；可选 `context_route`, `date_from`, `date_to`, `query` |
| `status` | 可选进度提示（兼容旧前端） | `message` |
| `step` | 分析阶段推进 | `step`, `status`, `analysis_id`, `message` |
| `token` | **仅 Verify 成功之后** | `text` |
| `done` | 正常结束 | `answer`, `analysis_id`；可选 `usage`, `verify` |
| `error` | 不可恢复失败 | `message` |
| `replace` | 整段替换（少用） | `answer` |

禁止再发明新的顶层 `type`（除非升 major）。

### 2.1 `step` 字段

```json
{
  "type": "step",
  "step": "route|retrieve|group|source|cross|verify",
  "status": "running|completed|skipped|error|timeout",
  "analysis_id": "hex",
  "report_id": "hex",
  "message": "人话短句（可给日志；UI 可不展示）",
  "group_id": "g1",
  "parent_analysis_id": null
}
```

- `source` 可对每组多次：`running` → `completed|timeout|error`
- `cross` 不需要时发一次 `status=skipped`（不要假装跑完）
- `timeout`：该阶段超时后用兜底继续时仍发 `timeout`（不是静默 `completed`）
- `error`：该阶段失败但流水线仍可降级继续；整题失败用顶层 `error`

### 2.2 `done` 字段

```json
{
  "type": "done",
  "answer": "...",
  "analysis_id": "hex",
  "report_id": "hex",
  "usage": {"llm_calls": 0, "source_groups": 0, "elapsed_ms": 0, "path": "...", "cross": false},
  "verify": {"verified": 0, "rejected": 0, "downgraded": 0, "flags": []}
}
```

`context_refs` **写入会话 meta / 服务端存储**，默认不塞进面向用户的 SSE（避免泄露内部结构）；需要调试时可加 `debug=1`。

## 3. `context_refs` 结构

```json
{
  "analysis_id": "hex",
  "parent_analysis_id": null,
  "last_user_q": "上一用户问句（仅助检索改写）",
  "entities": ["…"],
  "teams": ["…"],
  "issues": ["…"],
  "chunk_ids": ["…"],
  "item_ids": ["…"]
}
```

- Follow-up：继承上列结构化字段 → **新** `analysis_id` → 重跑 Retrieve…Verify  
- 禁止继承：历史 Answer 正文、历史 Cross summary 自然语言、历史 claims 文本

## 4. HTTP

- `POST /api/ask` JSON：同步完整结果（含 `analysis_id` / `context_refs`）
- `POST /api/ask/stream`：SSE，`Content-Type: text/event-stream`
- 请求体可含：`q`, `analysis`（bool 开关）, scope 字段（team/session…）

## 5. 前端约定

- 用户可见进度用阶段胶囊映射 `step`，**不展示** `message` 技术细节、`report_id`、refs
- `token` 到达前保持骨架屏；之后切流式正文
- 收到 `error`：展示失败文案，不拼半截 token

---

## 附录：流程现状审计（2026-08-28）

### 已落地

| 能力 | 状态 |
|------|------|
| Router independent / followup | ✅ |
| followup 继承 context_refs，Answer 不进成文 | ✅ |
| chunk_ids soft boost / hint | ✅ |
| Source 并行 ≤4 | ✅ |
| Cross 按需 / skipped | ✅ |
| Verify 后才 token | ✅ |
| 阶段 status 契约 | ✅ |
| 结构化空结果 team 名 | ✅ |
| 用户侧「思考中」动画（无技术阶段胶囊） | ✅ |
| 成文禁用「已校验断言」等内部话术 | ✅ |
| `ask_analyses` 按 analysis_id 持久化 | ✅（完成态） |

### 仍存在的问题（按优先级）

1. **SSE 断开仍会取消请求**  
   前端 AbortController + 连接结束即停；需改为任务入队 + SSE 仅订阅（`ask_analyses.status=running|completed` 已可承接）。

2. **Claim→Evidence 仍偏弱**  
   Verify 多为词面重合 + citation 句子过滤；未强制每条 claim 绑定 `chunk_id`/`evidence.ref`。

3. **期数窗口固定偏粗**  
   结构化默认 ~90 天；未做简单 2 期 / 普通 4 期 / 复杂 6–8 期 / 异步扩展。

4. **Citation 难一键回原文**  
   有来源句与条目 ID，但 UI 未稳定跳到原文锚点。

5. **素材去重不完整**  
   hit 层有 dedup；跨 Source 报告 / 结构化+向量重复陈述仍可能出现。

6. **权限隔离**  
   team_scope / zone_hard 有，但 Ask 会话与飞书多端一致性、跨人 refs 泄漏防护未系统验收。

7. **结构化交集「0 条」体验**  
   话术已修；若事实表 section/时间窗过严会导致真 0 命中。

8. **`status` 事件与 `step` 双轨**  
   仍发兼容用 `status`；长期应只保留 `step`。
