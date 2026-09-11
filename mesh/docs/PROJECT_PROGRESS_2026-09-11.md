# GeekPark Mesh · 全量进度总览

> **日期：** 2026-09-11（架构修订同日）  
> **HEAD：** 以 git 为准（日历 UAT 等已续上 `a21c7fc` 一带）  
> **分支：** `feat/relation-pipeline-v2-embedding`  
> **环境：** [tmesh](https://tmesh.geekpark.net) · [prod](https://mesh.geekpark.ai)  
> **用途：** 当前唯一「全量进度」快照；覆盖周报质量轨、Colleague v4 架构、Hands、记忆/隔离、验收与未决项。  
> **原则：** 只写可核对事实；状态用 ✅ Done / 🔨 In Progress / ⏸ Deferred / ⛔ Banned / ❌ Not Started / ⚠ Ops-blocked。  
> **Colleague 现行架构：** [`COLLEAGUE_AGENT_V4.md`](./COLLEAGUE_AGENT_V4.md)（废止 v2 Stage 1–4 线性计划）。

---

## 0. 一句话结论

Mesh 今天是 **两条轨道**：

| 轨道 | 状态 | 一句话 |
|------|------|--------|
| **A · 周报质量 / Grounded Brain** | ✅ **冻结**（v3.0 Production Baseline） | Retrieval / Ranking / Claim / Evidence 不再为 UX 解冻；Ask 只作 Capability |
| **B · Colleague Agent** | 🔨 **主战场 → v4** | v3 Wave1 + Hands + Identity/UAT 为底座；目标态 = **Agentic Enterprise**：Brain → Complexity Judge →（Direct \| Planner+Bounded ReAct+Specialists）→ 一张嘴 |

**当前主矛盾：** 把 **固定 Architecture**（含 Company Understanding）+ **运行时 Execution Complexity**（Simple→Complex）设计进底层并验收；外加开放平台权限发版与真人 Canary。  
**Colleague 轨允许（有笼子）：** Bounded ReAct · Planner（只出图）· Specialist Multi-Agent（不对用户说话）· **Company Ontology + Company Wiki（语境派生，非 Ask 替换）**。  
**仍绝对不做：** 无限 ReAct · 用户可见多 Agent 话术 · Wiki/ES/Neo4j **替换** Published Ask · 长期向量 Memory · 解冻质量轨 · `docker cp` 当发布 · Planner 当第二大脑 · **把 Complexity 档重新当成 Stage 路线图**。

---

## 1. 双轨产品地图（不得混修）

```
┌─────────────────────────────────────────────────────────────┐
│ A. Quality / Grounded Brain（冻结）                           │
│    Source → Pipeline → Preview → Relation → Publish          │
│    Ask: Identity → Permission → Retrieval → Ranking → Claim  │
│    Evidence · Temporal · Published-only                      │
└─────────────────────────────────────────────────────────────┘
                              ▲
                              │ Tool 调用（只读 Published）
┌─────────────────────────────────────────────────────────────┐
│ B. Colleague Agent v4（主战场）                               │
│    Identity → Company → Session → Colleague Brain            │
│         → Complexity Judge → Direct Tool | Planner/DAG       │
│         → Bounded Executor + Specialists → Verify → 一张嘴   │
│    Hands / Ask / Org = Capability；写仍 prepare→confirm      │
│    成文分栏：FACT / ANALYSIS / OPINION / SUGGESTION           │
└─────────────────────────────────────────────────────────────┘
```

**纪律：** Agent 需求不得重开 Pipeline / Preview / Publish / Ranking / Claim；周报链仅维护。

权威文档：

| 文档 | 用途 |
|------|------|
| `docs/COLLEAGUE_AGENT_V4.md` | **Colleague 现行架构**（Orchestrator / Bounded ReAct / Specialists） |
| `docs/AGENT_ARCHITECTURE_V1.md` | Org→…→Tool→Agent 冻结分层 |
| `docs/AGENT_QUALITY_V2.md` | 质量线冻结说明 |
| `docs/FEISHU_AGENT_V1.md` | 飞书产品硬边界 |
| `docs/COLLEAGUE_AGENT_V3.md` | Wave1 底座（已并入 v4） |
| `docs/COLLEAGUE_FEISHU_HANDS.md` | Hands 10 项 / 分桶 / 确认闸 |
| `docs/FEISHU_PERSONAL_AUTH.md` | 双身份 / UAT |
| `docs/COLLEAGUE_MEMORY_ISOLATION.md` | 短期记忆 + 数据隔离契约 |
| `docs/ENVIRONMENT_REPRODUCIBILITY.md` | 镜像发布 / REPRO 硬门 |
| `docs/MESH_V1_AGENT_GO_NOGO.md` | Mesh 作 Agent 底座 = 候选 Go |
| `docs/MESH_AGENT_FULL_ACCEPTANCE.md` | 全层验收；Mesh+Agent v1 GO |
| `docs/PROJECT_STATUS.md` | 2026-09-10 看板（**部分字段已落后 HEAD**，以本文为准） |

---

## 2. 轨道 A · 周报生产与质量（冻结）

### 2.1 生产链

| 能力 | 状态 | 备注 |
|------|------|------|
| 建期 / 上传 / ZIP | ✅ | |
| Pipeline 抽取 | ✅ | ⑤区 / L3 硬拦 |
| Preview 渐进开门 | ✅ | 默认串行；可开 `MESH_PREVIEW_CARD_CONCURRENCY` |
| 要点卡 | ✅ | |
| 关系 Decision → Gate → Writer | ✅ | Claim Check **enforce**（`rule_v1.2`） |
| 关系候选 `card_bridge` | ✅ **够用封存** | 不再扩 Decision/Ranking/Claim |
| 团队徽章 | ✅ | |
| Owner 上线 / EDM | ✅ | |

### 2.2 Ask / Grounded Brain 质量线

| 能力 | 状态 |
|------|------|
| Identity / Permission / Scope | ✅ 冻结 |
| Retrieval + Ranking v1.4 | ✅ 冻结 |
| Claim Support v2.4c-2 | ✅ 冻结 |
| Evidence 必显 | ✅ 合同 |
| Temporal / Published-only | ✅ |
| Vector / Embedding 热路径 | ✅ **默认 OFF** |
| 质量微补丁 | ⛔ **禁止**（除非可复现错误答且规则层无法修） |

### 2.3 验收结论（质量轨）

| 报告 | 结论 |
|------|------|
| `MESH_V1_AGENT_GO_NOGO.md`（2026-09-07） | **候选 Go**；无阻塞项 |
| `MESH_AGENT_FULL_ACCEPTANCE.md` | Mesh+Agent v1 = **GO**；Layer 4 飞书 E2E 可开 |
| `eval/reports/FINAL_ACCEPTANCE_REPORT.md` | 检索 Golden **25/25**（E2E LLM/follow-up/SSE 未全跑） |
| `FINAL_ACCEPTANCE_REPORT.prod.md` | 检索 Prod PG **25/25** |
| `PHASE1_CLOSEOUT_REPORT.md` | Phase1 Ask 收口快照 |

**已知非阻塞限制（质量轨）：** T13 段标题边界；Ask E2E LLM / follow-up / SSE 未在 Go/No-Go 时全量重跑；关系召回部分缺口产品「封存」。

---

## 3. 轨道 B · Agent / Colleague / 飞书

### 3.1 演进时间线（已发生，勿回退）

| 阶段 | Commit 锚点 | 状态 | 说明 |
|------|-------------|------|------|
| Conversation Runtime | `fcff160` / `e8c6eb8` | ✅ | Session / Route / Failure UX |
| Colleague Behavior v1 | `465da71` | ✅ 薄壳 | 规则路由 + 闲聊 |
| Colleague Agent v1 | `95010ff` | ✅ 后被取代 | |
| Colleague v2 Stage 1 Controller | `642e651`…`1ffe3ab` | ⚠ **旁路归档** | 组件化路径；禁止继续堆 Controller |
| Colleague v2 Stage 2A Core | `d415627` | ⚠ **旁路归档** | response_mode 预算等 |
| **Colleague v3 Wave 1** | `8ce24ad` + 后续修复 | ✅ **当前主路径** | Safety → 一张嘴；Ask 当工具；`MESH_COLLEAGUE_V3=1` |
| Hands 路线锁定 | `2ae2653` | ✅ | Tool Contract 分桶 |
| Hands skeleton | `0504054` | ✅ | |
| Hands 10 项接线 | `8c71101` | ✅ | 确认闸写入 |
| Native OpenAPI | `f0c4762` | ✅ | 真测默认 backend=`native` |
| Confirm / pending 加固 | `07ab857`…`d629109` | ✅ | @提及、root_id、多 worker 磁盘共享 |
| Decide JSON salvage | `5600d26` / `566e91f` | ✅ | 禁止关键词枚举意图 |
| **短期记忆 + 隔离 + 公司内可读** | **`1337c66`** | ✅ | 见 §5 |

### 3.2 Colleague v3 Wave 路线图

| Wave | 目标 | 状态 |
|------|------|------|
| **Wave 1** | SafetyGate → 一张嘴 Colleague；Ask / Hands 作工具 | ✅ **Done（默认开）** |
| **Wave 2** | 会话内「懂你」（关系/习惯产品层） | ❌ Not Started |
| **Wave 3** | 长期 Memory（人级、可审计） | ❌ Not Started；**长期向量 Memory ⛔ Banned** |
| 短期工作记忆（会话槽） | `active_goal` / `last_block` / `pending` | ✅ 已在 Wave1 上叠加（`1337c66`），**不等于 Wave 3** |

主路径控制流：

```
飞书事件 → feishu_bot → runtime.handle_message
  → Identity / Permission / Context
  → SafetyGate
  → colleague_v3._decide（极短 JSON：situation + action）
       ├ speak          → 嘴（禁止假装 Hands 成功）
       ├ ask            → 一个读工具 → 按 source_tier 合成
       ├ prepare_write  → 预览 + pending + active_goal
       ├ confirm_write  → 真写入 → 清 pending/goal 或记 last_block
       └ cancel_write / refuse
```

**一轮默认一个副作用**；不做同轮 Ask∥Hands；不做独立 Decide Agent。

### 3.3 飞书 Bot Phase 1

| 能力 | 状态 |
|------|------|
| OAuth 网页登录 | ✅ |
| Bot 事件 `POST /api/feishu/bot/event` | ✅ |
| Encrypt + Verification（tmesh + prod） | ✅ |
| 思考卡 → Patch 终答（cardkit / legacy fallback） | ✅ Phase 1 Done |
| 私聊 + 群冒烟 | ✅ 做过 |
| Evidence 卡片交互增强 | ❌ Canary 后按需 |
| 飞书事件层压测 | ❌ |

应用 ID：`cli_aa0fcf683e385beb`（tmesh/prod 共用）。

### 3.4 Feishu Hands · 10 项能力

**代码：** ✅ 已接入。  
**开关：** 默认关；真测需 `MESH_FEISHU_HANDS=1`，写入另需 `MESH_FEISHU_HANDS_WRITE=1`。  
**Backend：** `native`（当前真测）| `cli`（辅）| `mock`（单测）| MCP URL（设计主路径，生产未切）。

| # | 能力 | 代码 | 备注 / 缺口 |
|---|------|------|-------------|
| 1–5 | `feishu.search`（doc/message/wiki/folder/calendar） | ✅ | 全库文档搜常需 **user_access_token**；缺则诚实失败 |
| 6 | `feishu.doc.get` | ✅ | 依赖 scopes |
| 7 | `feishu.calendar.list` | ✅ | 依赖 calendar scopes |
| 8 | `feishu.discuss.summary` | ✅ | 基于消息检索规范化 |
| 9 | Speak / 成文 | ✅ | **不是** Hands 写工具；属 Colleague 嘴 |
| 10a | `feishu.doc.create` | ✅ | 确认闸 + 创建后 `tenant_readable` |
| 10b | `feishu.im.send` | ✅ | 确认闸 |
| 10c | `feishu.calendar.create` | ✅ | 确认闸 |

| 横切能力 | 状态 |
|----------|------|
| 写前用户确认（`pending_write`） | ✅ |
| 跨 uvicorn worker 共享 pending（磁盘） | ✅ `d629109`；`MESH_UVICORN_WORKERS=1` 亦已收紧 |
| 群 `@` 提及剥离后确认匹配 | ✅ `07ab857` |
| 忽略 `root_id` 拆 session | ✅ `6077c99` |
| Decide JSON 截断 salvage（再请 LLM，非关键词枚举） | ✅ |
| 创建 docx 后公司内获链可读 | ✅ `1337c66`；API：`link_share_entity=tenant_readable` |
| sheet/bitable 同权限 helper | ✅ API 已通用（`type=sheet` 等）；**创建表格工具尚未单列产品能力** |
| 开放平台 scopes 勾选并**发版** | ⚠ **Ops-blocked**（无法服务端代开） |
| User OAuth（全库搜） | ❌ Not Started |
| MCP 作生产默认 runtime | ❌ Not Started（native 为当前停靠） |
| CLI 容器内登录 | ⏸ Partial |
| Phase 3「张三在忙什么」Ask∥Hands 分桶成文 | 🔨 未收口 |

Scopes 清单：`docs/FEISHU_HANDS_SCOPES.md`（含 `docs:permission.setting:write_only` / `drive:drive` 等）。

### 3.5 短期工作记忆与数据隔离（2026-09-11）

契约：`docs/COLLEAGUE_MEMORY_ISOLATION.md` · 实现：`session_state.py` + `colleague_v3.py` · Commit：`1337c66`

**数据隔离（硬门）**

| 桶 | `source_tier` | 用途 |
|----|---------------|------|
| published | `published` | 已上线周报事实 |
| feishu_live | `feishu_live` | Hands 读/写 |
| speak_draft | `model` | 闲聊/成文草稿，不进事实池 |

禁止跨桶拼成一条「企业事实」。

**短期工作记忆（会话 TTL，非长期记忆）**

| 字段 | 含义 |
|------|------|
| `recent_turns` | 近轮对话 |
| `pending_write` | 待确认写操作 |
| `active_goal` | 未完成目标 `{summary, family, tool, utterance}` |
| `last_block` | 上一刀失败 `{code, tool, message}`（`scope_denied` / `hands_off` / `empty` / `feishu_api` / `other`） |

**调度纪律**

1. 有 `pending_write` → 确认/取消优先  
2. 有未完成 `feishu_write` goal 且用户在催 → 回到 prepare/confirm，禁止闲聊搪塞  
3. `speak` 不得声称已调用 Hands / 已创建成功（输出硬拦）  
4. 工具失败 → 记 `last_block` + 固定收口话术  

**已实测示例：** 文档《Mesh 是谁》  
https://feishu.cn/docx/BCQodMvgyokof1x1SIsccJ7Tnmc  
已补 `tenant_readable`（公司内获链可读）。

---

## 4. 性能 / 容量 / 可复现部署

### 4.1 性能（tmesh · Vector OFF · 量级）

| 项 | 状态 | 大约数字 |
|----|------|----------|
| 单请求基线 | ✅ 量过 | 普通检索 P50 ~13s / P95 ~17s |
| Capacity C_safe | ✅ | 约 C=8 |
| 流式首 token / 队列 UX | ❌ | 未做 |
| 飞书事件层压测 | ❌ | |

### 4.2 发布流水线（强制）

```
本地 pytest/smoke → commit
  → ship_image.ps1 -Target tmesh
  → docker build geekpark-mesh:<date>-<sha> → digest → compose recreate
  → REPRO_STATUS=PASS → 冒烟
  →（通过后）ship_image.ps1 -Target prod
```

| 项 | 事实 |
|----|------|
| 脚本 | `mesh/deploy/ship_image.ps1` |
| tmesh | `104.250.53.182:22341` · `/opt/geekpark-tmesh` |
| prod | `/opt/geekpark-mesh` |
| 热修 | `ship_changed_files.ps1` 仅紧急 → 必须再用镜像路径收口 |
| ⛔ | 把 `docker cp` 当 Agent/Runtime 发布方式 |
| 纪律 | 未验 tmesh 不上 prod；默认不 Publish / 不全量 Ask / Preview |

规范：`docs/ENVIRONMENT_REPRODUCIBILITY.md`。  
**注意：** 仓库内部分 `ENV_REPRO_BASELINE.*.json` 可能落后 HEAD；以容器内 `REPRO_STATUS` / `/api/repro/status` 为准。`1337c66` 发版时 tmesh+prod 均为 **REPRO PASS**。

---

## 5. Canary / 真人验收

| 资产 | 结论 |
|------|------|
| `eval/reports/agent_canary/PRODUCT_READINESS.md` | **NO-GO**（早期 follow-up / 模糊失败） |
| `eval/reports/agent_canary_post_crs/PRODUCT_READINESS.md` | **CONDITIONAL GO**（follow-up 改善；A 残留 ×3 先不修） |
| `docs/AGENT_CANARY_2.md` | 🔨 **进行中**：真人四指标；本轮原则「只量不修」 |
| Hands 多轮自测 | ✅ 本地 `test_colleague_memory_schedule.py` + Hands 10 套件；tmesh 远程 share/memory smoke PASS |

**延迟体验：** 普通问答约 10–17s；尚无流式 TTFT。

---

## 6. 绝对禁令（修订后仍有效）

**Colleague 轨已解禁（须带笼子，见 v4）：** Bounded ReAct · Planner（只规划）· Specialist Agents（不对用户说话）。

**仍禁止：**

- 无限 `while true` ReAct / 无预算 tool loop  
- 用户可见「XX Agent 认为」多角色话术；Router/Answer/Critic 式拆人对外  
- Planner 做公司事实、最终人格回答或权限放行  
- 新 Retrieval 主路径 / 用 Wiki·ES·Neo4j **替换** Published Ask（质量轨）  
- **长期向量 Memory** / Dynamic Router  
- 为 Colleague UX **解冻** Ranking / Claim / 质量微补丁  
- 把 `feishu_live` / Wiki 语境 混进 `published` / enterprise_fact  
- 无用户确认（及 WRITE 开关）的飞书写入  
- 回写 Mesh 业务库 / 自动扩能力  
- Ask 事实绕过 Published-only  
- `docker cp` 当发布；无 digest / REPRO 的上线  
- 把产品中轴拉回 v2 Controller  
- 用关键词枚举用户句式替代上下文 LLM 调度（硬确认短句除外）

> **说明：** Colleague **Company Ontology / Company Wiki** 已写入 v4（懂结构/懂语境）；这与「Wiki 当 Ask 主路径」不是同一件事。

---

## 7. 未决项总表（按优先级）

### P0 · 近端 + v4 梯子（Company Understanding 前置，见 `COLLEAGUE_AGENT_V4.md` §9）

| 项 | 状态 | 说明 |
|----|------|------|
| 架构契约文档 v4 | ✅ | 含公司理解三角；§9 实施序已按栈序改正 |
| **Company Ontology v0** | ❌ | **先于** Orchestrator；结构先验注入 Brain/Judge |
| **Company Wiki v0** | ❌ | **先于** Specialist 空转；`wiki_context`；禁冒充 FACT |
| Session/Context 接线先验 | ❌ | Ontology+Wiki+Session 同一 Context |
| Brain 消费先验 | ❌ | 无先验不装懂 |
| TaskPlan + 只读 Orchestrator | ❌ | 在先验之后；写路径不动 |
| Complexity Judge 显式档位 | ❌ | Judge 读先验再分档 |
| Bounded ReAct 预算闸 | ❌ | 初值见 v4；Capacity 后校准 |
| Specialist 接口 | ❌ | 只消费先验 + Capability |
| Synthesis 分栏 FACT/ANALYSIS/OPINION/SUGGESTION | ❌ | 「有思想」落点 |
| Complex 金丝雀 tmesh 真聊 | ❌ | 群+人+日历+周报 |
| 开放平台 scopes **发版并生效** | ⚠ Ops | 勾选不够；须创建版本+发布 |
| Hands 真人多轮（创建文档→确认→公司内可开） | 🔨 | 代码就绪；依赖 scopes + 现场验 |
| Decide/调度稳健性（过渡期） | 🔨 | 至 Orchestrator 接管前持续真聊 |
| Canary 2.0 真人指标 | 🔨 | 只量不修 |

### P1 · 下一波产品

| 项 | 状态 |
|----|------|
| User OAuth 覆盖面（全库搜等；日历 UAT 已起步） | 🔨 |
| MCP 作 Hands 默认 runtime | ❌ |
| 会话内「懂你」（原 Wave 2，并入 Brain/Memory 层） | ❌ |
| 多实例共享 Session Store（扩容门） | ❌（现磁盘 pending + workers=1） |
| Observability（request_id / route / repair） | ⏸ Partial |
| 群边界现场 enforce | 🔨 / 计划中 |
| 创建「表格」一等公民工具（现仅 doc.create + 通用 share helper） | ❌ 产品未单列 |

### P2 · 明确延期

| 项 | 状态 |
|----|------|
| 长期 Memory（原 Wave 3） | ⏸ / ⛔ 向量方案 |
| Evidence 卡片 UX 打磨 | ⏸ Canary 后 |
| 流式首 token / 队列 UX | ❌ |
| 飞书事件层压测 | ❌ |
| Cards concurrency=2 默认化 | ⏸ |
| 模糊闲聊 A 残留抠死 | ⏸ 先不修 |

### 文档/认知债务

| 项 | 说明 |
|----|------|
| `PROJECT_STATUS.md`（09-10） | Hands/记忆镜像锚点已落后 → **以本文 + HEAD 为准** |
| `CURRENT_SYSTEM.md` | 部分表述（如飞书自动回复）可能过时，需 spot-check |

---

## 8. 测试与门禁速查

| 套件 / 门 | 状态 |
|-----------|------|
| Ask 检索 Golden / Prod 25/25 | ✅ 历史通过 |
| Colleague v3 wave1 / Hands 10 / memory schedule | ✅ 本地 pytest（`1337c66` 前后相关套件绿） |
| pending 多轮 smoke | ✅ `deploy/_hands_confirm_multiturn_smoke.py` 等 |
| tmesh/prod REPRO | ✅ 镜像发版硬门 |
| 只读页不得触发 Preview/Ask/Embed/Publish | ✅ 发布纪律 |

---

## 9. 环境与开关速查

| 变量 / 项 | 含义 |
|-----------|------|
| `MESH_COLLEAGUE_V3=1` | Colleague v3 主路径（默认开） |
| `MESH_FEISHU_HANDS` | Hands 总开关（默认关） |
| `MESH_FEISHU_HANDS_WRITE` | 允许确认后真写 |
| `MESH_FEISHU_HANDS_BACKEND` | `native` / `cli` / `mock` |
| `MESH_FEISHU_HANDS_MCP_URL` | MCP（未作默认） |
| `MESH_AGENT_SESSION_DIR` | Session/pending 落盘目录 |
| `MESH_UVICORN_WORKERS` | 建议 1（配合 pending 磁盘） |
| `MESH_CLAIM_CHECK_MODE=enforce` | 质量轨 Claim 强制 |
| Vector 热路径 | 默认 OFF |

---

## 10. 近期关键 Commit（Agent 主线）

```
1337c66  短期工作记忆 + 隔离调度 + 创建文档公司内可读
566e91f  去掉创建文档关键词枚举；salvage 仍走 LLM
5600d26  decide JSON 失败 salvage
d629109  pending 跨 worker 共享；decide 靠上下文
6077c99  去掉 root_id 拆 session；确认加固
07ab857  剥离 @_user 提及
72977eb  native API JSON 错误硬化
f0c4762  native OpenAPI Hands；真测可开
8c71101  Hands 10 项接线 + 确认写
0504054  Hands skeleton
2ae2653  Hands 路线 + Tool Contract 分桶锁定
8ce24ad  Colleague v3 Wave1
…（更早 v2/v1/Runtime 见 git log）
```

---

## 11. 「现在该干什么」执行序

1. **锁架构：** 以 `COLLEAGUE_AGENT_V4.md` 为准；**Company Understanding 前置**（§9），废止「先 Planner 后 Ontology」。  
2. **先 C0–C2：** Ontology v0 → Wiki v0 → Brain 注入；pytest + tmesh。  
3. **再 L0–L3：** TaskPlan / Judge / 金丝雀（群+人+日历+周报）。  
4. **开放平台：** scopes 发版 + Agent OAuth 回调域。  
5. **勿做：** 无先验空转 Specialist、解冻 Ranking/Claim、无限 ReAct、Wiki 冒充 FACT、跳过 tmesh 直推 prod。

---

## 12. 变更记录

| 日期 | 说明 |
|------|------|
| 2026-09-11 | 首版全量进度总览；对齐 HEAD `1337c66`；覆盖双轨、Hands、记忆隔离、验收与未决项 |
| 2026-09-11 | **路线修订：** 引入 Colleague v4 Agentic Enterprise；解禁有笼 Bounded ReAct/Planner/Specialists；废止 Stage 线性计划 |
| 2026-09-11 | **公司理解三角：** Ontology + Wiki + Grounding 写入 v4；区分 Claim Ontology 与 Company Wiki 笼子 |
| 2026-09-11 | **§9 改正：** Company Understanding 实施前置；废止先 Orchestrator 后 Ontology/Wiki |

---

*本文取代对 `PROJECT_STATUS.md`（2026-09-10）在 Hands/记忆/镜像锚点上的过时段落；旧文可保留作历史，新决策以本文 + git HEAD + 容器 REPRO 为准。*
