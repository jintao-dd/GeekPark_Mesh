# GeekPark Mesh · 项目现状一页纸

> **日期：** 2026-09-10  
> **读者：** 任何要快速上手的同事（产品 / 工程 / 对接方）  
> **原则：** 只写「现在真的怎样」，不写愿景。细节文档见文末索引。

---

## 0. 30 秒结论

Mesh 今天是两件事：

1. **周报生产线**：各部门素材 → 抽取 → 要点卡 → 草稿/关系 → Owner 确认上线  
2. **已上线语料上的问答（Agent/Ask）**：只能答 Published；要 Evidence / 期次

**当前主战场（已排期）：**

```
Phase 1 [P0] 飞书 Bot 基础闭环  ← 正在做 / 优先
  · 出站 im/v1/messages
  · <1s「思考中」Interactive Card → 后台 Agent → Patch 最终回答+Evidence
Phase 2 [P1] 关系召回饥荒（单独开刀）
  · 实体对齐 / 别名 / 跨团队共现
Phase 3 [P2] 性能与产线微调
  · Cards concurrency=2 稳定化
  · Web Ask 流式首 token 探索
体验收口：飞书 Bot 100% 可用（入站+出站+状态反馈）
```

| 轨道 | 状态 | 一句话 |
|------|------|--------|
| **质量（答得对不对）** | ✅ **已冻结** v3.0 | Ranking v1.4 + Claim Support v2.4c-2；禁止回头开质量小版本 |
| **运行时 / 飞书** | 🔨 **Phase 1** | 入站+解密✅ · **出站+思考卡（本迭代）** · 关系召回未修 |
| **关系召回** | 🔍 **Phase 2 排队** | 候选层饥荒（实体按团队隔离），不是 Decision 误杀 |

---

## 1. 系统是什么 / 不是什么

### 是
- 固定流水线 + 若干次 LLM（抽取、出卡、草稿、关系决策、问答）
- 真相源 = **已 publish** 的 items / relations / evidence
- 部署：Docker 镜像路径（commit → build → digest → recreate）；测试环境 tmesh，再生产

### 不是
- 通用知识库 / Wiki / ES / 图谱主路径  
- 带 Planner / ReAct / 自动扩能力的「大 Agent」  
- 秒回 IM（普通问答大约 **10–17 秒**，主耗在 Answer LLM）

---

## 2. 两条环境

| | tmesh（测试） | prod（生产） |
|--|--|--|
| URL | https://tmesh.geekpark.net | https://mesh.geekpark.ai |
| 用途 | 联调、压测、飞书 Bot 自测 | 正式周报与读者 |
| 纪律 | 改动先上这里 | **必须 tmesh 过了再上**；默认不瞎 Publish / 不跑全量 Ask |

环境可复现规范：`docs/ENVIRONMENT_REPRODUCIBILITY.md`  
发布脚本：`deploy/ship_image.ps1`（禁止把 `docker cp` 当正式发布）

---

## 3. 阶段看板（清晰对勾）

### A. 周报生产

| 能力 | 状态 | 备注 |
|------|------|------|
| 建期 / 上传 / RSS | ✅ | |
| Pipeline 抽取（含并行 extract） | ✅ | ⑤区/L3 硬拦 |
| Preview 渐进开门 | ✅ | 骨架可先进预览页 |
| 要点卡生成 | ✅ | 默认 **串行**；`MESH_PREVIEW_CARD_CONCURRENCY` 可开并行（tmesh 已 A/B） |
| 周报壳 + 关系 Decision→Gate→Writer | ✅ 链路在 | **本期待修：候选召回过少** |
| Owner 上线 / EDM | ✅ | |
| 关系「卡面有线索但候选=0」 | ❌ 未修 | 见 `eval/reports/RELATION_RECALL_GAP.2026-09-08.*` |

### B. Agent / Ask 质量

| 能力 | 状态 | 备注 |
|------|------|------|
| Identity / Permission / Scope | ✅ 冻结 | |
| Retrieval + Ranking v1.4 | ✅ 冻结 | |
| Claim Support v2.4c-2 | ✅ 冻结 | |
| Evidence 必显（可验证事实） | ✅ 合同 | |
| Vector / Embedding 热路径 | ✅ 默认 **OFF** | 与质量线一致 |
| 质量微补丁 / 新质量版本 | ⛔ 禁止 | 未开新刀前不要动 |

### C. Agent 性能 / 容量

| 能力 | 状态 | 大约数字（tmesh · Vector OFF · Sonnet） |
|------|------|------|
| 环境 Manifest / REPRO 门禁 | ✅ | |
| 健康单请求基线 | ✅ | 简单 ~2.5–6s；普通检索 P50~13s / P95~17s；强 claim ~8s |
| Capacity C_safe | ✅ 量过 | 约 C=8 量级（以报告为准） |
| 流式首 token / 队列 UX | ❌ | 未做；10s+ 体感偏慢 |
| Feishu 事件层压测 | ❌ | HTTP Agent 压测先做了 |

### D. 飞书

| 能力 | 状态 | 备注 |
|------|------|------|
| OAuth 登录网页 | ✅ | `FEISHU_APP_ID/SECRET` |
| 群 → 团队绑定 | ✅ | |
| Bot 事件订阅（开发者服务器） | ✅ 接线 | `POST /api/feishu/bot/event` |
| Encrypt Key 解密 + Verification Token | ✅ | tmesh 已配 |
| **Bot 出站 + 思考中卡片 → Patch 终答** | 🔨 Phase 1 | `feishu_api` / `feishu_cards`；`FEISHU_BOT_REPLY=1` |
| Evidence 卡片交互 / 更细状态机 | ❌ | Phase 1 最小可用后可增强 |
| 小范围真人 Canary | ❌ | 路线图 ⑥ |

### E. 明确不做（冻结禁止项）

新 Planner · 新 Retrieval 架构 · Wiki · ES · Neo4j 主路径 · Agentic RAG · Memory 架构 · 写回业务库 · 自动扩能力 · 大规模 Ontology · **质量小版本回头改**

---

## 4. 最近刚做完的工程刀（别和「质量」混谈）

| 刀 | 结果 | 默认是否打开 |
|----|------|----------------|
| 环境可复现 + ship_image | REPRO_STATUS 硬门禁 | ✅ 流程强制 |
| Answer/Semantic packing | 压 token；普通问仍 ~10s+ | ✅ 已在运行时 |
| Weekly Preview Job Profile | 总墙钟约数分钟级；Cards 曾是 Preview 主瓶颈 | 测量脚本 |
| Cards 受控并行 A/B | conc=2 卡片阶段约 **1.7×**；retry=0；输出因 LLM 非确定会变 | **默认仍 concurrency=1** |
| Relation Recall Gap 诊断 | raw 候选≈1；跨团队共现实体=0 | 未改代码 |
| Feishu 加密事件解密 | tmesh 可收加密回调 | ✅ tmesh；出站仍缺 |

---

## 5. 你现在若要接手：建议顺序

```
① 读本页（你在这里）
        ↓
② 自测问答：网页 Ask 或 POST /api/agent/v1/message（同大脑）
        ↓
③ 飞书：事件已能进 tmesh → 下一刀做「自动回消息」才算 Bot 闭环
        ↓
④ 关系空：单独开「候选召回 / 实体对齐」刀，不要和 Bot/性能绑一起
        ↓
⑤ 质量线继续冻；有 failure 再决定是否解冻
```

---

## 6. 关键入口（给会看代码的人）

| 事 | 入口 |
|----|------|
| HTTP 编排 | `app/main.py` |
| 挖掘 Job | `app/pipeline.py` |
| 预览 Job（含 cards 并行开关） | `app/preview_job.py` |
| 关系候选 | `app/relation_candidates.py` |
| Agent 大脑 | `app/agent/runtime.py` → `handle_message` |
| 飞书事件 | `app/agent/feishu_bot.py` · `POST /api/feishu/bot/event` |
| 飞书展示文案 | `app/agent/feishu_reply.py` |
| 发布 | `deploy/ship_image.ps1 -Target tmesh\|prod` |

---

## 7. 文档索引（按需下钻）

| 文档 | 用途 |
|------|------|
| **本文 `PROJECT_STATUS.md`** | **阶段与缺口总览（先看这个）** |
| `CURRENT_SYSTEM.md` | 周报/Ask 流水线细节（部分段落可能略旧，飞书以本文为准） |
| `FEISHU_AGENT_V1.md` | 飞书产品硬边界与路线顺序 |
| `AGENT_ARCHITECTURE_V1.md` | Agent ①–⑧ 架构冻结稿 |
| `ENVIRONMENT_REPRODUCIBILITY.md` | 镜像 / digest / REPRO 门禁 |
| `RUNTIME_EXECUTION.md` | Vector OFF、Answer/Semantic 运行时默认 |
| `AGENT_QUALITY_V2.md` / Go-NoGo | 质量基线与门禁 |
| `eval/reports/AGENT_PERF_BASELINE_V1.tmesh.*` | 性能数字 |
| `eval/reports/CARDS_PARALLEL_AB.2026-09-08.*` | Cards 并行 A/B |
| `eval/reports/RELATION_RECALL_GAP.2026-09-08.*` | 关系召回缺口 |

---

## 8. 一句话给外人

> Mesh 周报生产与「有证据的问答」已经能在 tmesh/prod 跑；**答案质量基线已冻**。  
> 工程上正在收口体验与通道：**飞书能收到消息但还不能自动回复**；关系卡偏少是候选召回问题，已诊断未修。  
> 下一步优先 Bot 出站闭环与可预期等待，而不是改 Ranking/Claim。
