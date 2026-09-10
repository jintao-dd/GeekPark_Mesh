# GeekPark Mesh · 现状与后续计划（完整）

> **日期：** 2026-09-10  
> **读者：** 产品 / 工程 / 对接方  
> **原则：** 只写「现在真的怎样」+「下一步做什么」；不写愿景空话。  
> **镜像锚点：** `geekpark-mesh:2026-09-10-03204493a7e1`（tmesh + prod 已对齐）

---

## 0. 30 秒结论

Mesh 今天是两件事：

1. **周报生产线**：素材 → 抽取 → 要点卡 → 草稿/关系 → Owner 上线  
2. **已上线语料上的问答（Agent / 飞书）**：只读 Published；回答要 Evidence / 期次

| 轨道 | 状态 | 一句话 |
|------|------|--------|
| Mesh v1 质量 | ✅ **冻结** v3.0 | Ranking v1.4 + Claim Support v2.4c-2；禁止质量微补丁 |
| Mesh v1 生产链 | ✅ **候选 Go** | 可作 Agent 底座（见 `MESH_V1_AGENT_GO_NOGO.md`） |
| 关系召回工程刀 | ✅ **够用封存** | `card_bridge` 已上；不再扩 Decision/Ranking/Claim |
| 飞书 Agent Phase 1 | ✅ **Done** | 入站+解密+出站思考卡→终答；tmesh 冒烟 + prod 已上 |
| P0.0 Meta short-circuit | ✅ **Done** | 「你是谁」等不进 Retrieval；commit `0320449` |
| **当前主战场** | → **Conversation Runtime Sprint** | Session Context + Route + Failure UX + Help；不解冻质量 |

```
P0.0 Meta/Identity ✅
P0.1 全谱 Canary → NO-GO（B/A/F/E）
Conversation Runtime Sprint ← 现在（一次集中：B+A+F+E）
     ↓
自动回归全谱 Canary
     ↓
真人 Canary（repair_rate + 自然追问）
不做：Wiki / Memory 大架构 / ReAct / 解冻 Ranking·Claim·Retrieval
```

---

## 1. 系统是什么 / 不是什么

### 是
- 固定流水线 + 若干次 LLM（抽取、出卡、草稿、关系决策、问答）
- 真相源 = **已 publish** 的 items / relations / evidence
- 部署：Docker 镜像路径（commit → build → digest → recreate）；**先 tmesh 再 prod**

### 不是
- 通用知识库 / Wiki / ES / 图谱主路径  
- 带 Planner / ReAct / 自动扩能力的「大 Agent」  
- 秒回 IM（普通问答大约 **10–17 秒**，主耗在 Answer LLM）

---

## 2. 两条环境（当前事实）

| | tmesh | prod |
|--|--|--|
| URL | https://tmesh.geekpark.net | https://mesh.geekpark.ai |
| 镜像 | `2026-09-10-03204493a7e1` | 同左 |
| REPRO | PASS | PASS |
| Claim Check | `enforce` | `enforce` |
| 密码登录 | ✅ 可开（staging） | ❌ 关（`MESH_ENV=production`） |
| 飞书事件钥 | ✅ | ✅ **已与 tmesh 同步**（2026-09-10） |
| 纪律 | 联调 / 冒烟 | 默认不 Publish、不跑全量 Preview/Ask |

发布：`deploy/ship_image.ps1 -Target tmesh|prod`  
规范：`docs/ENVIRONMENT_REPRODUCIBILITY.md`

---

## 3. 能力看板（现在）

### A. 周报生产

| 能力 | 状态 | 备注 |
|------|------|------|
| 建期 / 上传 / RSS | ✅ | |
| Pipeline 抽取 | ✅ | ⑤区/L3 硬拦 |
| Preview 渐进开门 | ✅ | |
| 要点卡 | ✅ | 默认串行；可开 `MESH_PREVIEW_CARD_CONCURRENCY` |
| 关系 Decision→Gate→Writer | ✅ | Claim Check enforce；文案须过 grounding |
| 关系候选 `card_bridge` | ✅ **封存** | 实体 key + 卡面桥接 + 海外已沟通→编辑部 |
| 团队徽章 | ✅ | 同队不再「实线 + → 同队」 |
| Owner 上线 / EDM | ✅ | |

**论证口径：** 读者可见卡 = 有 evidence + body/detail 过闸 + claim 未越界（rule_v1.2）。  
虚线「→ 队」= 建议关注，不声称该队已有一手记录。

### B. Agent / Ask 质量（冻结）

| 能力 | 状态 |
|------|------|
| Identity / Permission / Scope | ✅ 冻结 |
| Retrieval + Ranking v1.4 | ✅ 冻结 |
| Claim Support v2.4c-2 | ✅ 冻结 |
| Evidence 必显 | ✅ 合同 |
| Vector / Embedding 热路径 | ✅ 默认 OFF |
| 质量微补丁 | ⛔ 禁止 |

### C. 性能 / 容量

| 能力 | 状态 | 大约数字（tmesh · Vector OFF · Sonnet） |
|------|------|------|
| REPRO 门禁 | ✅ | |
| 单请求基线 | ✅ | 普通检索 P50~13s / P95~17s |
| Capacity C_safe | ✅ 量过 | 约 C=8 |
| 流式首 token / 队列 UX | ❌ | 未做 |
| 飞书事件层压测 | ❌ | HTTP 压测先做了 |

### D. 飞书 Agent

| 能力 | 状态 | 备注 |
|------|------|------|
| OAuth 登录网页 | ✅ | |
| Bot 事件订阅 | ✅ | `POST /api/feishu/bot/event` |
| Encrypt + Verification | ✅ | tmesh + **prod 已同步** |
| 出站 + 思考卡 → Patch 终答 | ✅ **Phase 1 Done** | 私聊 + 群（CCC技术组）冒烟过 |
| 小范围真人 Canary | 🔨 **P0.1 进行中** | 像不像同事；见 `AGENT_CANARY_P01.md` |
| Evidence 卡片交互增强 | ❌ | Canary 后按需 |

### E. 明确不做

新 Planner · 新 Retrieval · Wiki · ES · Neo4j 主路径 · Agentic RAG · Memory 架构 · 写回业务库 · 自动扩能力 · 大规模 Ontology · **质量小版本回头改** · 放宽 provenance 造假双边

---

## 4. 2026-09-10 收口记录（本轮）

| 项 | 结果 |
|----|------|
| tmesh 冒烟 | Preview ✅ · 飞书私聊 ✅ · 飞书群聊 ✅ · 关系卡 ✅ · REPRO PASS |
| prod ship | `2fc80f3884a7` · REPRO PASS |
| prod 最小验收 | healthz ✅ · webhook ✅ · Agent 问答 ✅ · 关系卡展示 ✅ · 无全量 Preview/Publish |
| 飞书事件钥 | prod ← tmesh 已同步并 recreate |
| 关系召回 | **够用封存**（Gap 仍可 partial；「尚未接触」不硬造） |
| 飞书 Phase 1 | **Done** |

关键 commit（工程）：`c1b60c8` / `c5f5ad9`（card_bridge）· `e02bc14`（staging 密码登录）· `2fc80f3`（徽章去重）· 文档 `f9eed33`+本文

---

## 5. 后续计划（按优先级）

### P0 · 现在就做（严格顺序）

| # | 工作项 | 验收 |
|---|--------|------|
| **0.0** | Agent 基础行为 short-circuit | ✅ Done（`0320449` / 镜像 `03204493a7e1`） |
| **0.1** | **真人 Canary（像不像同事）** | 3～5 人×10～20 问 + 群；记真实聊天与行为信号；**本轮不修**；见 `AGENT_CANARY_P01.md` |
| 0.2 | 可观测固化 | request_id / intent / latency / evidence / refuse / model |
| 0.3 | 群边界实战 | 群绑定团队生效；不越权 |
| 0.4 | 失败手册 | 超时 / 解密 / 无证据 / 拒答处置 |

**P0.1 第一性指标：用户会不会自然问下一句。** 重复 failure 模式出现前不集中修。
### P1 · Canary 稳定后按需

| # | 工作项 | 触发条件 |
|---|--------|----------|
| 3.1 | 飞书事件层压测 | Canary 无 P0 通道故障后 |
| 3.2 | 队列 / 超时 UX | 用户抱怨「卡住」或并发撞墙 |
| 3.3 | Evidence 卡片交互 | 真人反馈「看不清出处」 |
| 3.4 | Cards concurrency=2 默认化 | 再跑一轮 A/B 无质量回退 |

### P2 · 明确延后 / 不排期

| 项 | 原因 |
|----|------|
| 关系召回再深挖（别名图谱、尚未接触造卡） | 产品已够用封存 |
| Ask E2E LLM 全量重跑 | 非阻塞；择机补 |
| T13 段边界白名单 | Known limitation |
| 统一 Entity 大图 / Neo4j | 架构远期，不在周报层硬补 |
| ReAct / LLM Planner | 架构禁止 |

### 决策闸门

- **质量解冻：** 仅当 Canary 出现可复现的「答错 / 无证据乱答」且规则层无法修  
- **关系再开刀：** 仅当 Owner 明确「缺卡影响上线」且能指出漏召回类型  
- **prod 发布：** 必须 tmesh 冒烟过；默认不 Publish、不迁数据

---

## 6. 接手顺序（给下一个人）

```
① 读本文（完整现状 + 计划）
        ↓
② 打开 tmesh / prod healthz，确认 REPRO_STATUS=PASS、镜像 tag 一致
        ↓
③ 飞书私聊 + 群各问 1 句（真实 Bot，不经 harness）
        ↓
④ 按 `AGENT_CANARY_P01.md` 跑真人 Canary（记聊天，不修）
        ↓
⑤ 每日扫 observability / 日志失败类
```

---

## 7. 关键入口

| 事 | 入口 |
|----|------|
| HTTP 编排 | `app/main.py` |
| 预览 Job | `app/preview_job.py` |
| 关系候选 | `app/relation_candidates.py` |
| 关系闸门 / Claim | `app/relation_gate.py` · `app/relation_claim_check.py` |
| Agent 大脑 | `app/agent/runtime.py` → `handle_message` |
| 飞书 Bot | `app/agent/feishu_bot.py` · `feishu_api.py` · `feishu_cards.py` |
| 发布 | `deploy/ship_image.ps1 -Target tmesh\|prod` |

---

## 8. 文档索引

| 文档 | 用途 |
|------|------|
| **本文 `PROJECT_STATUS.md`** | **现状 + 后续计划（先看）** |
| `FEISHU_AGENT_V1.md` | 飞书产品硬边界与路线 |
| `AGENT_ARCHITECTURE_V1.md` | Agent ①–⑧ 架构冻结 |
| `MESH_V1_AGENT_GO_NOGO.md` | Mesh v1 候选 Go |
| `ENVIRONMENT_REPRODUCIBILITY.md` | 镜像 / REPRO |
| `RUNTIME_EXECUTION.md` | Vector OFF 等运行时默认 |
| `eval/reports/RELATION_RECALL_GAP.2026-09-08.*` | 召回缺口留档 |
| `eval/reports/AGENT_PERF_BASELINE_V1.tmesh.*` | 性能数字 |

---

## 9. 一句话给外人

> Mesh 周报与「有证据的问答」已在 tmesh/prod 可跑；**答案质量基线已冻**。  
> **飞书 Bot Phase 1 已完成**（能收、能回、有思考卡）；关系召回工程刀**够用封存**。  
> **下一步：P0.1 真人 Canary（像不像同事）。收集真实对话后再打共性产品问题。**
