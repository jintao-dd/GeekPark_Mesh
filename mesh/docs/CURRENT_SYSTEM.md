# GeekPark Mesh · 当前系统说明

> 写的是 **2026-09-07 代码里实际在跑的系统**（Relation 两阶段 + `decision_tier` + 已上线可 Preview 不空窗 + `publish_lane` 写入边界 + 段级/关系指纹缓存），不是规划。  
> 部署操作看 `README.md`；更细的模块索引看 `ARCHITECTURE_HANDOVER.md`（部分段落可能略旧，以本文为准）；知识图谱方案看 `KNOWLEDGE_GRAPH_DESIGN.md`（设计，未全面落地）。

---

## 1. 这是什么

Mesh 是极客公园内部用的两件事：

1. **做一期沟通情报周报**：把各部门的会、沟通记录、选题、RSS 放进某一期，抽成条目，生成五块结构的周报，owner 确认后上线，并可发 EDM。
2. **在已上线周报上提问**：问「谁接触了谁、各团队在看什么、两边有没有交集」。答案只能来自已 publish 的语料。

它不是通用知识库，也不是 Agent。Ask 不读草稿、不读未上线素材。

---

## 2. 谁用、从哪进


| 角色            | 入口                               | 做什么               |
| ------------- | -------------------------------- | ----------------- |
| editor        | `/admin/issue/{slug}`            | 建期、上传、挖掘、生成预览、改草稿 |
| owner         | 同一控制台第四步                         | 确认上线              |
| admin         | `/admin/users`、`/prompts`、`/edm` | 账号、提示词、EDM        |
| viewer / 飞书登录 | `/{slug}`、`/archive`、读者页 Ask     | 读周报、搜索、问答         |


飞书目前只有 **OAuth 登录** 和 **群→团队绑定**。没有飞书 Bot 收消息答问。

Agent v1：**架构 ①～⑦ 已冻结**；正在实现本地/HTTP Harness（`/api/agent/v1/message`）与契约测试，**通过后再接 ⑧ 飞书接线**。详见 `docs/AGENT_ARCHITECTURE_V1.md`。

后台四步：放入素材 → 挖掘 → 审校（要点卡 + 草稿）→ 确认上线。

---



## 3. 总体结构

两条链路，共享「已上线」数据，之后分开走。

```
编辑 / 读者
    │
    ▼
FastAPI  mesh/app/main.py
    │
    ├──────── 周报（人手按钮 + 异步 Job）────────────────────┐
    │  上传 → pipeline 挖掘 → preview 出稿                    │
    │    （要点卡 → 周报壳 → Relation Decision→Gate→Writer）   │
    │  → owner 上线 → 建索引 → 异步 embedding → 可选发 EDM     │
    │                                                          │
    └──────── Ask（请求内同步）────────────────────────────────┐
       Guard → 规则路由 → 检索 → 分析成文                       │
                                                                ▼
                              PostgreSQL / SQLite
                         issues / sources / items / cards
                         draft_json / published_json
                         embedding_status*
                         search_fts / item_facts / chunks
                         ask_sessions / ask_analyses
```

生产 Docker：`postgres` + `mesh`（8090→8080，默认 2 worker）+ `mesh-worker`。  
本地默认 SQLite + 任务在 web 进程里跑线程。

性质：**固定流水线 + 若干次 LLM 调用**。没有 Planner、没有 ReAct、没有 tool loop。

---



## 4. 周报怎么生产



### 4.1 触发

全是人手点按钮，**没有按周 cron**。


| 动作            | 接口                                       | 怎么跑                         |
| ------------- | ---------------------------------------- | --------------------------- |
| 上传 / 粘贴 / RSS | `/admin/issue/{slug}/upload|paste|fetch` | 同步，写入 `sources`             |
| 挖掘            | `POST .../pipeline/start`                | 异步 Job `kind=pipeline`      |
| 生成预览          | `POST .../preview/start`                 | 异步 Job `kind=preview`       |
| 上线            | `POST .../publish`                       | 同步，仅 owner；**不等** embedding |
| 向量补齐          | 上线后 `embed_job.ensure_for_slug`          | 异步 Job `kind=embed`         |
| 发信            | 上线后 `edm_job.enqueue_auto_send`          | 异步 Job `kind=edm`（可关）       |




### 4.2 挖掘（`pipeline._run`）

对每个有正文的 source 调一次 `llm.extract_source`，写入 `items`，再跑代码步：

- 归属：`owner_guard` / `resolve_item_owner`
- ⑤区、L3：抽取时 `zone_hard.apply_hard_blocks`，`blocked=1`
- 跨通道合并：`merge.apply_merge`（同团队 + 实体重叠 + 文本相似）
- 首次进入：对照 `entities.first_issue`
- 禁用词扫描：`llm.forbidden_hits`
- 旧草稿作废：`db.mark_draft_stale`

`pipeline.py` 里列了约 25 步给 UI 看；`by=defer` 的不在挖掘阶段执行。真正干活的是抽取和上面这些代码步。  
`transcribe` **没有 ASR**，只统计标题含「录音」的 T6。  
`relation-link` / `compose` / `cite` 等标成 `defer`，挖掘阶段只打「待预览」。

T13 混合包：`aggregator.split_bundle_ex` 按章节切开再分别抽取。

### 4.3 点「生成预览」之后（详细）

入口：控制台素材页主按钮 `#to2`（`console.js`）。  
接口：可能先 `POST .../pipeline/start`，再 `POST .../preview/start`。

#### A. 前端怎么串（人只点一次）

```
点「生成预览」
  ├─ 已有 items 且来源未脏
  │     → 直接 startPreparePreview（preview Job）
  │     → 文案：「正在生成要点卡与草稿…」
  │
  └─ 还没有 items，或 sourcesDirty
        → setAutoPreview(true)
        → runMining（pipeline Job）
        → 文案：「正在挖掘与脱敏…」
        → pipeline done 后自动再调 preview/start
```

忙态下再点「生成预览」：提示任务进行中，**不再**强制重挖（避免大文档再次卡死）。卡住时建议 Ctrl+F5 后重试。

#### B. 若触发了挖掘（`pipeline._run`）

见上一节：抽 items → 归属/⑤区 → merge → 草稿 stale。  
**挖掘阶段不做关系卡**（`relation-link` / `compose` 等标 `defer`）。

#### C. 预览主流程（`preview_job._run`）——核心

**渐进式（2026-09）**：结构性闸门 + merge 后立刻写骨架 `draft_json`（`_preview_partial_ready`），Job 置 `preview_ready` + `preview_url`，**可先进入预览页**；后台继续出卡 / 周报壳 / 关系。全部完成才 `_preview_gate_ok`。上线仍只认 gate_ok；生成中 `_preview_building` 禁止发布。

进度文案大致：`跨通道合并…` → `已可进入预览，正在生成要点卡…` → `要点卡 i/N` → `周报草稿与关系…` → `完成`。

```
1) 闸门（同前：items / 未挖掘 / 聚合拆段 / 归属）
2) merge.apply_merge
3) 写骨架稿 → preview_ready（先进预览）
4) 要点卡（每团队 1 次 LLM；条目指纹未变则复用旧卡）
5) 周报壳 LLM + 关系两阶段
6) filter_ungrounded → publish_blockers 结构性检查 → finalize gate_ok
7) 落库 / 预览页自动刷新
```

---



### 4.4 关系卡怎么来（Decision → Gate → Writer）

**当前主链路（2026-09）**：代码候选 + LLM 决策 + 代码闸门 + LLM 写作；`decision_tier` **必须写入草稿**。

```
items（未拦截、未合并）
  → build_relation_candidates          [代码]
  → assign_candidate_ids
  → Decision LLM                       [llm.build_relation_decisions_with_coverage]
       keep/skip · label · relation_type · decision_tier · evidence_refs · reason
       提示词：issue_relation_decisions.md
       要求覆盖全部 candidate_id（缺 id 会重试/报错）
  → Evidence Gate                      [apply_evidence_gate]
       校验 refs → 拼 evidence → 锁 teams/sources
       keep 但无有效证据 → skip
       decision_tier 空则按 label 推断（normalize_decision_tier）
       输出 RelationObject（含 decision_tier / relation_type）
  → Writer LLM                         [relation_writer.write_relations]
       只写 title / body / details
       LOCKED_FIELDS 含 decision_tier、relation_type、label、evidence…
       提示词：issue_relation_writer.md
  → Claim Check（rule_v1）             [relation_claim_check.apply_claim_checks]
       检查 Writer 原文 title+body+details（强度等级 vs 证据）
       默认 shadow：写 `_claim_check` / `_relation_claim_audit`，不藏卡
       MESH_CLAIM_CHECK_MODE=enforce 时 drop invalid
  → 后处理
       去弱重复、narrative verify、attach_reader_flags
       filter_draft_relations、无 evidence 丢弃
       split_relations_for_publish → _relations_reader / _relations_backlog
       verify_issue_draft
       finalize_decision_audit → _relation_decision_audit
```

`decision_tier` **取值**：`strong` / `parallel` / `watch` / `skip`。  
读者可见（`reader_visible`，**派生字段**）：卡完整（evidence + title/body）且非 `skip` → 读者可见；`strong` / `parallel` / `watch` **只影响展示排序**，不再做「仅 strong 上读者页」。  
投影入口：`build_published_projection(draft)` → **仅 Publish** 写入 `published_json`。Preview / 编辑只写 `draft_json`（读者切片在上线时才物化）。  
论证不足的卡在生成预览时 **直接隐藏**（`filter_ungrounded_relations`），不拦整期进预览。

**Preview：** 可在 `status=published` 上直接跑；只写 `draft_json`，不改 `published_json`、不 reindex Ask。读者/Ask 仍看线上版。可选 `POST .../create_revision` 用线上稿铺底草稿（保持 published）。Owner Publish 才替换 `published_json` 并重建索引。

**写入硬边界（**`publish_lane`**）：** 任何 `UPDATE issues SET … published_json` 须在 `allow_published_write` 内；`MeshConnection.execute` 运行时拦截。静态扫描见 `tests/test_published_write_boundary.py`。Publish 是唯一业务入口（`write_publish_projection`）。

**壁钟缓存：**

- 多源抽取：job LLM 池内并行 preheat（`pipeline._run`）。
- T13 段级：`segment_cache` + `§sd:{digest}|` pointer；未变段 reuse，变段/新段 LLM，删段自然丢弃。
- 周报壳+关系：`_relation_input_fp`（candidates × item_ids/evidence/snippets × team_cards × prompt 文件 hash）；`force` / gate_stale / building / `_stale` 均不复用。

关系卡上常见字段：


| 字段                                                      | 谁写                             |
| ------------------------------------------------------- | ------------------------------ |
| `label` / `teams` / `sources` / `evidence` / `item_ids` | Gate 锁死                        |
| `decision_tier` / `relation_type`                       | Decision → Gate → Writer 锁死    |
| `title` / `body` / `details`                            | Writer                         |
| `reader_visible`                                        | Display 代码                     |
| `_relation_decision_audit`                              | 整期审计（coverage、by_tier、drop 原因） |


上线闸门仍走 `relation_gate` / `issue_verify` / attribution blockers（弱关系确认、证据不足等）。

---



### 4.5 上线之后

`published_json` = 当时草稿副本（读者正式版）。同时：

- 冻条目快照 `published_items_snapshot`
- `register_entities`（名字 + first_issue）
- `reindex_issue`：FTS / `entity_team_facts` / `item_facts` **在 Publish 请求内同步**（Ask 立即可用）；`chunk_index` **异步**补建；向量仍走 `embed_job`（不挡上线）
- `embed_job.ensure_for_slug`：异步补 embedding；`embedding_status`：`pending → running → completed|partial|failed`
- 可选 SMTP：`edm_job.enqueue_auto_send`（关自动则只排队后跳过）

读者页跟 `published_json`；Ask 跟索引（FTS/facts 同步就绪；hybrid 向量可稍后）。向量未完成不挡上线与 Ask（检索可退回 FTS）。



## 5. Ask 怎么回答

入口：`POST /api/ask`、`POST /api/ask/stream`。默认开分析（`MESH_ASK_ANALYSIS=1`）。

```
ask_rate（默认 40 次/分钟）
  → ask_scope（web / 飞书 / 团队 / 期号）
  → ask_context.route          独立问 or 追问（规则，看指代词）
  → ask_engine.prepare
       guard 拦乱码
       parse_intent 规则分流
       structured SQL  或  FTS + item_facts + 可选向量
  → 无命中：固定「未找到」，不调 LLM
  → 有命中：ask_analysis 固定 6 步
       route → retrieve → group → source* → cross? → verify
  → ask_citation 按「实体×团队」删无依据句
  → 写入 ask_messages / ask_analyses / ask_log
```

检索两条路：


| 路                | 何时                 | 读什么                              |
| ---------------- | ------------------ | -------------------------------- |
| structured       | 问句命中差集/交集/按团队/海外缺口 | `entity_team_facts`              |
| hybrid / lexical | 其余，或 structured 失败 | FTS + `item_facts`；配了 embed 再加向量 |


追问只继承上一轮的 `context_refs`（实体、团队、chunk_id、item_id），**不把上一轮答案当证据**。

分析最多 4 组 × 6 条。成文前用词面重合过滤 claim，再 LLM 写成段。  
SSE 会发 step 事件；简单路径会先收完全文再校验，再按块吐出，不是校验后的真 token 流。

---



## 6. 数据长什么样

开发默认 SQLite；生产 PostgreSQL。**没有外键约束。** 没有独立向量库，向量是 `chunk_embeddings.vector_json`。

### 生产对象（表）


| 表                                  | 作用                                                                                    |
| ---------------------------------- | ------------------------------------------------------------------------------------- |
| `issues`                           | 一期周报。`draft_json` / `published_json` / 快照；`embedding_status` **等向量进度列（schema 1.8.4）** |
| `sources`                          | 原始材料（T1–T13）                                                                          |
| `items`                            | 抽出来的原子条。`owner_team`、`blocked`、`merged_into`、`entities`（JSON 文本）                      |
| `cards`                            | 各团队要点卡 JSON                                                                           |
| `entities`                         | 名字登记 + `first_issue`。`aliases` 列存在，**基本没人用**                                          |
| `search_fts`                       | 已上线章节正文                                                                               |
| `entity_team_facts`                | 从 published_json 拆的「谁×哪队×哪一节」                                                         |
| `item_facts` / `item_entity_facts` | 从条目快照做的检索行                                                                            |
| `chunk_index` + `chunk_embeddings` | 统一检索块 + JSON 向量                                                                       |
| `ask_sessions` / `ask_messages`    | 多轮对话；refs 在 `meta_json`                                                               |
| `ask_analyses`                     | 一次分析的答案、verify、evidence（运行时建表）                                                        |
| `mesh_jobs`                        | 后台任务                                                                                  |




### 只活在 JSON / 内存里的对象


| 名字           | 在哪                                          | 说明                                                                  |
| ------------ | ------------------------------------------- | ------------------------------------------------------------------- |
| 周报「Report」   | `issues.published_json`                     | 五块结构，不是独立表                                                          |
| 关系卡          | `draft_json` / `published_json.relations[]` | 展示字段 + evidence + `decision_tier`                                   |
| 关系候选         | 生成时内存                                       | `build_relation_candidates`；Decision 审计在 `_relation_decision_audit` |
| Ask Evidence | `ask_analyses.sources_json`                 | `{ref, quote}`，组内编号，不绑 item/chunk                                   |
| context_refs | 分析表 + 消息 meta                               | 追问种子                                                                |


周报和 Ask **没有**统一的 Entity / Relation / Evidence 图。共享的是 `items` 和 `published_json`。上线后单向拆进事实表给 Ask 用。

---



## 7. 现在有 / 没有

**有**

- 人手周报生产、要点卡、五块草稿、上线闸门、EDM  
- 团队名归一（GP → Global Partnership 团队 等）  
- **Relation 两阶段**：候选 → Decision → Gate → Writer；`decision_tier` **落草稿**  
- 关系 JSON 带 `item_id` / `source_id` / snippet / `decision_tier` / `relation_type`  
- 上线与 Embedding **解耦**（异步 `embed_job`，管理台可见进度）  
- 已上线语料的 FTS / 结构化差集交集 / 可选向量  
- 乱码拒答、无命中拒答、句级归属过滤  
- 禁用词字段改写、弱关系必须人确认  
- **Relation Gold v2**（`eval/relation_gold_v2.jsonl`）：统一 schema + `claim_valid` / `claim_invalid` 对抗样例 + lexical/gate/claim baseline  
- **Claim Check**（`relation_claim_check` rule_v1.2）：Writer 原文 title/body/details 强度守卫；**tmesh/prod 已 `MESH_CLAIM_CHECK_MODE=enforce`**（invalid 藏卡）

**没有**

- 周报自动周更  
- Agent / ReAct / LLM Planner  
- 公司别名合并、人名归一、entity resolution  
- 关系价值排序（有 tier，但无独立打分模型）  
- 人工改稿回流到下一期规则  
- 跨期关系 id（只有 `first_issue` 和上一期 lead 进 prompt）  
- 点回原文的统一 Evidence 层  
- 飞书群问答、录音转写

### 7.1 质量打磨轨道 → Agent Go/No-Go（2026-09-07）

质量轨道 **已收口**。正式总验收见 `MESH_V1_AGENT_GO_NOGO.md`。

| # | 项 | 状态 |
|---|----|------|
| ④ | Relation Gold v2 | ✅ |
| ① | Claim Check | ✅ **enforce**（tmesh+prod，`rule_v1.2`） |
| ② | Ask structured intent | ✅ **正式关闭**（Golden/Prod 检索 25/25） |
| ③ | T13 Segment / Attribution Quality | 🟡 **基本通过**（5/5 cases）；`### 商业化团队 · …` 边界为 Known limitation，不阻塞 |
| — | **Agent Readiness** | 🟢 Arch ①–⑦ ✅冻结；实现按 §9.3 七类场景验收；⑧ 待测通后接线 |

**非阻塞留档：** Ask E2E/Follow-up/SSE 本轮未重跑；统一 Evidence/Entity 图留给 Agent 架构，不在 Mesh v1 硬补。

**停止**：继续零散加 Mesh AI 能力。**当前**：Organization → Identity → …（不问「Mesh 还缺什么模型能力」）。

---



## 8. 部署与限制


| 项      | 现状                                         |
| ------ | ------------------------------------------ |
| 生产     | `docker compose`：PG16 + mesh + mesh-worker |
| 本地     | `./run.sh` / `run.bat`，SQLite              |
| Ask 并发 | compose 默认每 web worker 2，全局 4              |
| 分析规模   | 最多 4 组 × 6 条                               |
| 默认检索窗  | 约 90 天                                     |
| 速率     | 40 问 / 分钟 / 用户                             |
| Schema | 代码 `1.8.4`（含 embedding_* 列）                |


Ask 评测：2026-08-30 曾 22/25；Planner-lite 后 2026-08-31 全链路 25/25。**2026-09-07 回归保险**：Golden **25/25** + Prod PG **25/25**（检索；E2E 本轮未跑）→ ② 关闭。

---



## 9. 关键文件


| 路径                                      | 职责                                                        |
| --------------------------------------- | --------------------------------------------------------- |
| `docs/MESH_V1_AGENT_GO_NOGO.md`         | Mesh v1 → Agent 候选 Go 总验收（五层 + 非阻塞项）              |
| `docs/AGENT_ARCHITECTURE_V1.md`         | Agent：①～⑦ 架构冻结；⑦ Harness/契约测试中；⑧ 待测通后接线 |
| `app/pipeline.py`                       | 挖掘                                                        |
| `app/preview_job.py`                    | 要点卡 + 周报壳 + 触发关系两阶段                                       |
| `app/relation_candidates.py`            | 候选构建；`merge_relations_from_candidates` 入口                 |
| `app/relation_decision.py`              | Decision 编排 + Evidence Gate + `build_relations_two_phase` |
| `app/relation_writer.py`                | Writer；锁 `decision_tier` 等字段                              |
| `app/relation_display.py`               | tier 归一、`reader_visible`、reader/backlog 切分                |
| `app/relation_decision_audit.py`        | 决策审计 / human report                                       |
| `app/relation_gate.py`                  | 上线前关系检查                                                   |
| `app/relation_claim_check.py`           | Claim Check rule_v1（Writer 原文强度守卫；shadow/enforce）       |
| `app/embed_job.py`                      | 上线后异步向量                                                   |
| `app/llm.py`                            | 抽取 / 要点卡 / 周报壳 / relation decisions / writer / 问答         |
| `app/ask_engine.py`                     | 检索编排                                                      |
| `app/ask_analysis.py`                   | 多源分析成文                                                    |
| `app/retriever.py` / `qa_structured.py` | hybrid / 结构化查询                                            |
| `app/db.py` / `schema_pg.sql`           | 库（含 embedding 列）                                          |
| `app/job_worker.py`                     | 异步任务                                                      |
| `eval/run_final_eval.py`                | 25 题验收                                                    |


LLM 提示词在 `app/prompts/`：`extract_T*.md`、`card_team.md`、`issue_draft.md`、`issue_draft_candidates.md`、`issue_relation_decisions.md`、`issue_relation_writer.md`、`91_output_issue.md`、`qa.md`。

---



## 10. 和其它文档的关系


| 文件                          | 用途                                                                |
| --------------------------- | ----------------------------------------------------------------- |
| **本文**                      | **当前系统怎么跑**（含 Preview 详细节）                                        |
| `FULL_PIPELINE_DETAIL.md`   | **端到端逐步详解**：建期→Ask，含接口/提示词/库表/限制/索引与向量                            |
| `MESH_BASELINE_v1.md`       | Preview**优化契约（完整版）**：proxy 命名、实验条件冻结、Grounding 硬底线、P95、Eval Delta |
| `AI_EVAL_POLICY.md`（仓库根）    | PR 强制评测纪律                                                         |
| `CONTRIBUTING.md`（仓库根）      | 贡献入口，指向 Eval Policy                                               |
| `README.md`                 | 怎么部署、怎么每周点按钮                                                      |
| `ARCHITECTURE_HANDOVER.md`  | 模块级交接、RAG 逐步对照                                                    |
| `ASK_ANALYSIS_CONTRACT.md`  | Ask SSE / context_refs 契约                                         |
| `KNOWLEDGE_GRAPH_DESIGN.md` | 关系资产层的设计（未当主链路用）                                                  |
| `AGENT_EVOLUTION.md`        | Agent 化讨论（未实现）                                                    |
| `RELATION_EDITOR_RUBRIC.md` | 关系人审 rubric + **Gold v2** 契约 / baseline                             |


