# GeekPark Mesh · 项目完整架构

> 基于 **2026-08-31 当前代码**，不是设计愿景。  
> 代码事实源：`mesh/app/`、`mesh/eval/`、`mesh/tests/`、`mesh/docker-compose.yml`。  
> 设计文档（`AGENT_EVOLUTION.md`、`ASK_ANALYSIS_CONTRACT.md`）只作对照，**未实现的一律标「未实现」**。  
> 生产库精确行数、线上是否已部署 Query Guard：**未确认**。

---

# 1. 项目定位

## 系统解决什么问题

极客公园内部的 **周报生产 + 已上线语料问答** 系统。

两件事绑在同一套数据上：

1. **生产一期沟通情报周报**：各部门把例会、沟通记录、选题、RSS 等素材放进某一期（`issues.slug`），系统抽取原子条目、生成团队要点卡与五块结构草稿，owner 确认后上线，并可发 EDM。
2. **在已上线周报上提问**：读者/编辑用自然语言问「谁接触了谁、各团队关注什么、两边有没有交集」，答案必须能回到 published 语料，不能靠模型自由发挥。

它不是通用知识库，也不是开放域 Agent。检索范围默认是 **已 publish 的周报 JSON / 条目快照 / 事实表 / chunk**，不是草稿、不是未上线素材。

## 用户主要使用场景

| 角色 | 入口 | 实际做什么 |
|------|------|------------|
| editor | `/admin/issue/{slug}` 四步控制台 | 建期 → 上传/粘贴/抓 RSS → 点挖掘 → 点生成预览 → 改草稿 |
| owner | 同上第四步 | `POST /admin/issue/{slug}/publish` 确认上线 |
| admin | `/admin/users`、`/admin/prompts`、`/admin/edm` | 账号、提示词、EDM 名单 |
| viewer / 飞书登录用户 | `/{slug}`、`/archive`、读者页 Ask | 读已上线周报、搜索、多轮问答 |

飞书目前只做 **OAuth 登录**（`auth.feishu_authorize_url` / `feishu_exchange`）和 **群→团队绑定**（`feishu_chat_bindings`）。没有飞书 Bot 收消息答问的实现。`README.md` 写「预设推送待飞书 Bot」——代码里 `presets.py` 有调度表，**推送通道未完成**。

## 当前核心能力

- 素材入库：上传 / 粘贴 / RSS（`ingest.read_upload`、`ingest.fetch_feed`）；T13 混合包按段拆分（`aggregator.split_bundle_ex`）
- 挖掘管线：每来源一次 `llm.extract_source`，再跑归属、⑤区硬拦、跨通道合并（`pipeline._run`）
- 周报生成：要点卡 `llm.build_team_card` + 草稿 `llm.build_issue_draft`（`preview_job._run`）
- 上线闸门：`main.publish_blockers` + `relation_gate.relation_publish_blockers`
- 发刊后索引：`db.reindex_issue` → `search_fts` / `entity_team_facts` / `item_facts` / `chunk_index`
- 混合检索：FTS + item_facts + 可选向量（`retriever._hybrid_recall`）
- 结构化交叉查询：差集/交集/按团队/海外缺口（`qa_structured.parse_intent` + `run_structured`）
- 多源分析成文：固定 6 步 `route→retrieve→group→source→cross?→verify`（`ask_analysis.run_analysis`）
- 多轮追问：只继承 `context_refs`，不把上一轮 Answer 当证据（`ask_context.route`）
- 异步任务：pipeline / preview / edm / embed（`job_store` + `job_worker`）

## 不包含哪些能力

| 常被误以为有 | 代码事实 |
|--------------|----------|
| 周报自动周更 / cron | **没有**。只有人手点按钮。`presets.run_due_pushes` 是 Ask 预设，不是周报 |
| Agent / ReAct / tool loop | **没有**。Ask 与周报都是固定流水线 + 离散 LLM 调用 |
| LLM 查询改写 / 学到的 rerank | **没有**。改写是规则拼接；rerank 是词面加分 |
| 独立向量库（pgvector / Pinecone） | **没有**。向量存在 `chunk_embeddings.vector_json`（JSON 文本），暴力余弦 |
| 真正的录音转写 | `pipeline` 的 `transcribe` 步只统计 T6 标题含「录音」的条数，**不调用 ASR** |
| 飞书群里直接问 Mesh | 只有登录与 team scope，**无 Bot 入站** |
| 对草稿/未上线素材问答 | Ask 只读 published 索引 |
| 把 TODO / DESIGN 步当成已执行 | `pipeline.DESIGN` 里大量 `by="defer"`，挖掘阶段只写「待预览/随抽取」 |

---

# 2. 总体架构图

## 2.1 双主链路

系统不是一张图能画完的一条河，而是 **两条主链路共享同一套 published 数据**：

```
■ 周报生产（异步为主）

编辑后台
  → FastAPI /admin/*
  → sources 入库（同步）
  → mesh_jobs(kind=pipeline)  ──异步──►  llm.extract_source + 代码步
  → mesh_jobs(kind=preview)   ──异步──►  要点卡 + 草稿
  → owner publish（同步）
       ├─ issues.published_json + published_items_snapshot
       ├─ db.reindex_issue（FTS / facts / chunks）
       └─ mesh_jobs(kind=edm) ──异步──►  SMTP

■ Ask 问答（请求内同步，分析内部用线程池）

读者页 / POST /api/ask|/api/ask/stream
  → ask_rate / ask_scope
  → ask_turn.begin_turn          Router（规则）
  → ask_engine.prepare           Guard + Intent + Retrieval
  → ask_analysis.run_analysis    或 ask_turn.generate_answer
       group → source* → cross? → verify/compose
  → ask_citation.validate_answer
  → ask_store / ask_messages / ask_log
  → UI（SSE step + token）
```

## 2.2 用户入口 → UI 全链路（Ask）

```
用户入口
  GET /{slug}  GET /archive  读者页 JS
       │
       ▼
API（同步 FastAPI def，会阻塞 worker）
  POST /api/ask              main._run_ask
  POST /api/ask/stream       main.api_ask_stream
  GET  /api/ask/analysis/{id} 回放
       │
       ▼
Ask 编排
  ask_rate.allow
  ask_scope.resolve
  ask_turn.begin_turn ──► conversation.ensure_session / recent_messages
                      ──► ask_context.route          【Router，规则，非 Planner】
  ask_turn.prepare_turn ──► ask_engine.prepare
       │
       ├─ Guard     ask_query_guard.guard_direct_answer
       ├─ Rewrite   ask_context.expand_search_q + embeddings.expand_query（别名，非 LLM）
       ├─ Intent    qa_structured.parse_intent（规则）
       └─ Retrieval retriever.retrieve
              ├ structured → qa_structured.run_structured（SQL，entity_team_facts）
              └ hybrid     → _hybrid_recall（FTS + item_facts + 可选向量）
       │
       ▼
Analysis（默认 MESH_ASK_ANALYSIS=1）
  ask_analysis.run_analysis
    group_contexts
    _analyze_one_source × N     【LLM】
    _cross_analyze?             【LLM，条件】
    _verify_and_compose         【代码校验 + LLM 成文】
    ask_citation.validate_answer
       │
       ▼
Storage
  ask_analyses（ask_store.start/finish）
  ask_messages.meta_json.context_refs
  ask_log
       │
       ▼
UI
  非流式 JSON 或 SSE：step / token / meta
```

## 2.3 同步 vs 异步

| 流程 | 模式 | 入口 |
|------|------|------|
| Ask 整段 | **同步**（分析内部 `ThreadPoolExecutor`） | `_run_ask` / `api_ask_stream` |
| 上传/粘贴/抓取 | 同步 HTTP | `main` upload/paste/fetch |
| 挖掘 | **异步** `job_kind=pipeline` | `pipeline.start` |
| 预览（卡+草稿） | **异步** `job_kind=preview` | `preview_job.start` |
| `/cards` `/draft` | 同步备用（会堵住 HTTP） | `main` |
| 上线 | 同步 | `main.publish` |
| EDM | **异步** `job_kind=edm` | `edm_job.enqueue_auto_send` |
| 向量回填 | **异步** `job_kind=embed` | `embed_job.start` |

`MESH_JOB_INLINE=1`（本地默认）：claim 后在 web 进程起 daemon 线程。  
`MESH_JOB_INLINE=0`（compose）：写入 `mesh_jobs._executor=pending`，由 `python -m app.job_worker` 领取。

## 2.4 LLM 调用位置（仅已实现）

| 位置 | 函数 | 用途 |
|------|------|------|
| `llm.extract_source` / `extract_items` | 挖掘 | 每来源 JSON 条目 |
| `llm.build_team_card` | 预览 | 每团队要点卡 |
| `llm.build_issue_draft` | 预览 | 整期五块 JSON |
| `llm.call_json_compliant` | 上述包装 | 禁用词重写 |
| `ask_analysis._analyze_one_source` | Ask | 每组抽取 facts/evidence |
| `ask_analysis._cross_analyze` | Ask | 多源交叉（可 skip） |
| `ask_analysis._verify_and_compose` | Ask | 成文 |
| `llm.answer_question` | Ask 简单路径 / 预设 | 单次 QA |
| `edm._highlight_from_llm` | EDM | 可选主题行 |
| `embeddings.embed_one` | 检索 | **嵌入 API，不是 chat LLM** |

周报路径 **没有** Planner、没有 Reflection loop、没有 tool calling。

## 2.5 数据库读写位置

**Ask 热路径读**：`search_fts`、`item_facts` / `item_facts_fts`、`chunk_index`、`chunk_embeddings`、`entity_team_facts`、`ask_sessions`、`ask_messages`、`feishu_chat_bindings`。

**Ask 热路径写**：`ask_sessions`、`ask_messages`、`ask_log`、`ask_analyses`、`ask_rate_hits`、`mesh_llm_slots`。

**周报路径读/写**：`issues`、`sources`、`items`、`entities`、`cards`、`edits`、`versions`、`mesh_jobs`、`mail_log`。上线后写索引表。

---

# 3. 核心模块说明

## Ask 系统

性质：**固定编排 + 规则路由 + 可选多源 LLM 分析**。不是 Agent。

调用关系：

```
main._run_ask / api_ask_stream
  ask_turn.begin_turn → ask_context.route
  ask_turn.prepare_turn → ask_engine.prepare
       ask_query_guard.guard_direct_answer
       ask_query.retrieval_query → ask_context.route
       qa_structured.parse_intent
       retriever.retrieve → run_structured | _hybrid_recall
  [direct_answer] 或
  [analysis] ask_analysis.run_analysis_collect
  [simple]   ask_turn.generate_answer → llm.answer_question
  ask_turn.complete_turn
       conversation.append_turn
       ask_store.finish
       ask_engine.log_ask
```

### ask_engine（`mesh/app/ask_engine.py`）

| | |
|--|--|
| **输入** | `con`, `q`, `AskScope`, 可选 `search_q` / `context_refs`。`history` 参数保留但 **不用于检索** |
| **输出** | `mode` ∈ `guard\|structured\|hybrid\|lexical`；`contexts`；可选 `direct_answer`；`n_hits` / `latency_ms` |
| **调用** | 被 `ask_turn.prepare_turn`、`presets.run_preset` 调用。自己调 guard / parse_intent / embed_one / retrieve |
| **限制** | 不调 chat LLM。seed chunk/item 最多 24。`intent.type=="error"` 分支在当前 `parse_intent` 下 **不可达** |

### ask_context（`mesh/app/ask_context.py`）

| | |
|--|--|
| **输入** | 当前 `q` + 最近 messages |
| **输出** | `route()` → `{kind: independent\|followup, search_q, context_refs, parent_analysis_id, reason}`。`history` **恒为空列表** |
| **关键函数** | `is_followup`（指代词/短句）、`expand_search_q`（拼 last_user_q + entities[:5] + teams[:2]）、`build_context_refs`、`needs_cross` |
| **限制** | 指代规则是词表，不是模型。独立专名短句（如「面壁智能」）故意不判追问。`needs_cross` 是关键词启发式 |

### ask_analysis（`mesh/app/ask_analysis.py`）

| | |
|--|--|
| **输入** | `q` + `prepare()` 的结果 |
| **输出** | generator 逐步 `ask_protocol.step_event`；最终 `{answer, analysis_id, verify, sources, context_refs, usage}` |
| **步骤** | 硬编码：`ask_protocol.STEPS = route, retrieve, group, source, cross, verify` |
| **LLM** | `_analyze_one_source`（max_tokens=900）、`_cross_analyze`（1400）、`_verify_and_compose`（1400） |
| **限制** | 最多 4 组 × 6 条（`MESH_ANALYSIS_MAX_GROUPS/PER_GROUP`）。结构化路径跳过 per-source LLM，用 `_reports_from_structured_contexts`。**不会因答案差而重检索**。`history` 被故意丢弃 |

### ask_citation（`mesh/app/ask_citation.py`）

| | |
|--|--|
| **输入** | 成文 + contexts |
| **输出** | `{answer, flags, removed}`：删掉「实体×团队」在证据里站不住的句子 |
| **实现** | 规则，无 LLM。`contexts_evidence` + 句切分 |
| **限制** | 只拦归属对，不拦一般事实幻觉；不绑定 `chunk_id` |

### ask_turn（`mesh/app/ask_turn.py`）

| | |
|--|--|
| **输入** | 用户、scope、问句、cache 回调 |
| **输出** | 一轮完整 HTTP/SSE 响应 |
| **限制** | `generate_answer_stream` **先收完全文再 citation，再按 48 字切块 yield**，不是校验后的真 token 流。独立问可缓存 120s；多 worker 时缓存关闭 |

### retriever（`mesh/app/retriever.py`）

| | |
|--|--|
| **输入** | `q`, `AskScope`, 可选 `intent` / `query_vec` / seed ids |
| **输出** | `(mode, hits, meta)`，`mode` 为 `structured` 或 `hybrid` |
| **调用** | `run_structured` 失败则 fallback `_hybrid_recall` |
| **限制** | 默认 limit=40。rerank 是规则加分（`rerank_hits`），分数越小越好（BM25 风格）。无 cross-encoder |

### structured query（`mesh/app/qa_structured.py`）

| | |
|--|--|
| **输入** | 原始用户问句（不要用扩展后的 `search_q`，`ask_engine.prepare` 已分开） |
| **输出** | `parse_intent` → `None` 或 `{type: diff\|intersect\|overseas_gap\|by_team, ...}`；`run_structured` 读 `entity_team_facts` |
| **限制** | 覆盖面窄。e08「跟进了」不匹配「跟进过」；e10 topic 必须在「各团队」前；e21「重叠」不是交集触发词。默认窗口 **90 天**（`qa_structured.DEFAULT_WINDOW_DAYS`：`MESH_QA_WINDOW_DAYS` 或 `MESH_QA_LEXICAL_WINDOW_DAYS` 或 `"90"`）。无结果走 `empty_result_answer`，不调 LLM |

### 其余 Ask 支撑

| 模块 | 作用 | 限制 |
|------|------|------|
| `ask_protocol.py` | SSE 步骤名与事件形状 | 步骤表写死 |
| `ask_scope.py` | web/飞书/期号/团队 scope | 团队来自 payload 或飞书绑定或用户 team |
| `ask_store.py` | `ask_analyses` 持久化 | **不在 `schema_pg.sql`**，运行时 `ensure_table` |
| `ask_rate.py` | 默认 40 次/分钟，失败放行 | |
| `ask_concurrency.py` | 进程信号量 + `mesh_llm_slots`；Ask 槽与 Job 槽分开 | Ask 默认并发 2，全局 4 |
| `ask_query.py` | `retrieval_query` → `route().search_q` | 薄封装 |
| `conversation.py` | `ask_sessions` / `ask_messages` | 最近 6～8 条；refs 在 meta_json |
| `owner_guard.py` | Ask 热路径只用 `parse_section_team`、`_title_entities` | 主体在周报侧 |
| `zone_hard.py` | **不进入 Ask 成文** | 只在抽取 |

---

## 周报生成系统

**结论：固定 pipeline + 若干次 LLM 调用。不是 LLM workflow 引擎，更不是 Agent。**

`mesh/app/pipeline.py` 自称「25 步唯一事实源」，但真正在 `_run` 里干活的只有可验收步；`by=defer` 在挖掘阶段**不执行**，控制台不得显示为已完成（见 P2-7 修复）。

| 实际执行 | 函数/动作 |
|----------|-----------|
| intake / normalize | 计数、标「已整理」 |
| transcribe | **只数 T6 标题含「录音」**，无 ASR |
| extract | `llm.extract_source` 循环 |
| owner-attrib | SQL 统计 + 写入时 `resolve_item_owner` |
| zone5-filter | 统计 `blocked=1`（硬拦在 `zone_hard.apply_hard_blocks` / extract 时已做） |
| pool-match / first-seen | 对照 `entities.first_issue` |
| cross-channel-merge | `merge.apply_merge` |
| lint | `llm.forbidden_hits` |
| contrib-list / trace-keep / draft-save | 计数 + `db.mark_draft_stale` |
| relation-link / sync-pick / compose / cite | **只标记「待预览/草稿阶段」** |
| name-verify / zone-sort / decision-split 等 | **只标记「随抽取提示词」** |

### 周报触发方式

人手：后台「挖掘」`POST /admin/issue/{slug}/pipeline/start`，「生成预览」`POST .../preview/start`。无 cron。

### 数据获取

- 上传 `ingest.read_upload`；粘贴；`ingest.fetch_feed`（极客公园中英 + `EXTERNAL_FEEDS`）
- 落 `sources`（`issue_id, stype, team, text, channel, meta`）
- 混合包上传时可能 `aggregator.sources_from_split` 预拆成多行

### 聚合逻辑

1. `aggregator.split_bundle_ex`：按 emoji/章节边界切 T1–T12
2. `merge.same_event` / `apply_merge`：同 `owner_team` + 实体重叠 ≥0.5 + 文本相似 ≥0.62 → `merged_into`
3. `relation_candidates.build_relation_candidates`：同一实体出现在 ≥2 个非「外部媒体」团队，且 `cross_team_provenance_ok`
4. `prepare_draft_bundle`：approved 要点卡 + 候选关系 + 首次进入名 + T7 外媒条目

### 分析逻辑

没有独立「分析 Agent」。交叉比较写在提示词 `issue_draft.md` 里，由 **一次** `build_issue_draft` 完成。代码侧只提供候选，不让模型自己发明跨团队关系（`issue_draft_candidates.md` + `merge_relations_from_candidates`）。

### 生成逻辑

`preview_job._run`：

1. 校验已有 items、来源都 extracted
2. `merge.apply_merge`
3. 每 `owner_team`（排除外部媒体）`llm.build_team_card` → upsert `cards`（自动 `status=approved`）
4. `prepare_draft_bundle` + 上一期 `published_json` 的 lead/关系标题
5. `llm.build_issue_draft` → `merge_relations_from_candidates` → `issues.draft_json`

输出字段见 `prompts/91_output_issue.md`：`question, lead, kpis, relations, contacts, keywords, plans, views, gaps, data_sources`。

### 校验逻辑

| 层 | 函数 | 何时 |
|----|------|------|
| ⑤区/L3 | `zone_hard.apply_hard_blocks` | 抽取后 |
| 禁用词 | `call_json_compliant` | 抽/卡/稿 |
| 归属缺失/非法 | `publish_blockers` | 上线前 |
| 草稿过期 | `db.draft_is_ready`（`_stale`） | 上线前 |
| 拆段低置信 | `llm.split_needs_review` | 上线前 |
| 必须有卡 | `publish_blockers` | 上线前 |
| 弱关系/出处 | `relation_publish_blockers` | 上线前 |
| 关系锁候选 | `filter_draft_relations` | 生成后 |

**没有**把草稿每个 `sources[]` 字符串回对 `sources.text` 的程序校验。

---

## Eval 系统

主入口：`mesh/eval/run_final_eval.py`（25 题，`--corpus golden|prod`，可选 `--e2e --followup --sse`）。

| 层 | 文件 | 覆盖 | 不足 |
|----|------|------|------|
| acceptance | `eval/run_acceptance.py` | 8 个场景，临时 SQLite + `_issue_2026-8-17.json`，**不调 LLM** | 单期导出；不管成文 |
| golden 25 | `eval/ask_eval_v1.jsonl` + `run_final_eval.py` | 路由、mode、关键词、scope、hits 上下限 | e08/e10/e21 仍 fail |
| E2E | jsonl 里 `e2e:true` 的 3 题（e02/e03/e05） | 真 LLM + verify rejected 上界 | 只有 3 问；耗时长、有波动 |
| follow-up | `eval/test_followup_e2e.py` | Q1→refs→「还有哪些」；禁止 Answer 泄漏 | 单场景 |
| SSE | `eval/test_sse_replay.py` | TestClient 断开后 `ask_store` 回放 | 生产容器曾缺 **httpx** 没跑成；**不要求** token 前缀一致 |
| 轻量 smoke | `eval/run_eval.py` | 扫 jsonl | **断言把 route.kind 和 expect_mode 比在一起，逻辑有误**，不要当验收 |
| pytest | `mesh/tests/test_ask_*.py` 等 | 路由、guard、citation、job、归属、关系候选 | **不跑 25 题 jsonl** |

**最近一次写入仓库的数字**

- 黄金 SQLite（`eval/reports/FINAL_ACCEPTANCE_REPORT.md`，2026-08-30 17:13）：检索 **22/25**，E2E **3/3**，follow-up PASS，SSE PASS。失败：e08、e10、e21（期望 structured，实得 hybrid）。
- 生产 PG（`FINAL_ACCEPTANCE_REPORT.prod.md`，2026-08-30 23:28）：检索 **20/24**（当时还没有 e25），E2E/SSE **未跑**，e20 仍失败（当时线上无 guard），另有 httpx 缺失。  
  **此后代码已加 `ask_query_guard`。生产是否已部署、是否重跑：未确认。**

---

# 4. 数据模型

**两套 schema 同源思路、无真实 FOREIGN KEY。**  
SQLite：`db.py` 内 `SCHEMA`，`SCHEMA_VERSION = "1.8.1"`。  
PG：`schema_pg.sql`。README 仍写 Schema 1.6.3，**文档过期**。

```
issues 1 ─── n sources
  │            │
  │            └── n items (source_id, merged_into→items)
  │                  │
  │                  ├── item_facts / item_facts_fts / item_entity_facts   （上线后）
  │                  └── chunk_index.item_id
  │
  ├── n cards (team)
  ├── draft_json / published_json          ← 这就是「Report」的落库形态
  └── published_items_snapshot

entities (name UNIQUE, first_issue)        ← 被 JSON 名引用，无 FK

entity_team_facts (issue_slug, name, team, section)   ← 结构化 Ask
search_fts (issue_slug, section, body)                ← 读者搜索 / FTS
chunk_index 1──1 chunk_embeddings                     ← 向量召回

ask_sessions 1──n ask_messages (meta_json.context_refs)
ask_analyses (analysis_id, sources_json, context_refs_json, verify_json)
     ↑ 运行时建表，PG 正式 schema 文件里没有
```

| 对象 | 是否独立表 | PK | 谁引用谁 | 用途 |
|------|------------|----|----------|------|
| **Issue** | `issues` | `id`；业务键 `slug` UNIQUE | 被 sources/items/cards/edits/versions/mail_log 用 `issue_id` | 一期周报容器；`draft_json` / `published_json` / 快照 |
| **Item** | `items` | `id` | `issue_id`→issues，`source_id`→sources，`merged_into`→自身 | 抽取原子条；`blocked` / `owner_team` |
| **Chunk** | `chunk_index` + `chunk_embeddings` | `chunk_id` TEXT | 逻辑指向 issue/item/source | 统一检索单元；向量 JSON |
| **Fact** | **无 `facts` 表** | — | 三张：`entity_team_facts`、`item_facts`、`item_entity_facts` | 结构化集合运算 + 条目检索 |
| **Entity** | `entities` | `id`；`name` UNIQUE | 无 FK；名字散落在 items.entities JSON 与事实表 | 首次进入、登记 |
| **Source** | `sources` | `id` | `issue_id`→issues | 原文；`stype` T1–T13 |
| **Evidence** | **不是表** | — | 活在 source report 的 `evidence[]`，写入 `ask_analyses.sources_json` | Ask 分析引用 |
| **Analysis** | `ask_analyses` | `analysis_id` | `parent_analysis_id` 自指；`session_id`→ask_sessions | 一次 Ask 运行 |
| **Context_refs** | **不是表** | — | JSON：`ask_analyses.context_refs_json`、`ask_messages.meta_json` | 追问种子（entities/teams/issues/chunk_ids/item_ids） |
| **Report** | **不是表** | — | 周报 = `issues.published_json`；Ask 的 source report = 内存 list，事后进 `sources_json` | 两种「报告」不要混 |

其他表：`users`、`settings`、`feishu_chat_bindings`、`user_ask_presets`、`preset_push_log`、`mesh_jobs`、`mesh_llm_slots`、`ask_rate_hits`、`ask_log`。

`migrate_to_postgres.py` 的拷贝表清单 **不含** `ask_analyses` / 部分 job 表；新 PG 靠 `schema_pg.sql` + 运行时 DDL。从旧 SQLite 迁历史 Ask 分析：**未确认是否丢过**。

---

# 5. RAG 完整链路

从用户 query 到答案。每步：**文件 · 函数 · 实现 · 是否真实验收**。

| 步 | 文件 / 函数 | 当前实现 | 真实验收 |
|----|-------------|---------|----------|
| **Query** | `main._run_ask` 读 `payload.q` | 原始字符串 | 25 题语料覆盖 |
| **Guard** | `ask_query_guard.is_nonsense_query` / `guard_direct_answer`；挂在 `ask_engine.prepare` | 无 CJK、非多词英文、长 alnum → 固定话术，不检索 | 黄金 e20/e25 **已过**。生产 08-30 报告 **未过**（当时无此代码） |
| **Intent** | `qa_structured.parse_intent` | 正则：by_team / overseas_gap / diff / intersect，否则 None | 部分。e01/e09 等 structured **过**；**e08/e10/e21 未过** |
| **Rewrite** | `ask_context.expand_search_q`；`embeddings.expand_query`；`retriever._chunk_hint_terms` | 追问拼接旧问+实体+团队；4 个中英别名；seed chunk 标题入 query。**无 LLM rewrite** | follow-up E2E **过**（测的是不泄漏 Answer，不是改写质量） |
| **Filter** | `retriever._resolve_date_window`、`search.lexical_date_range`、`AskScope.team_filter`、`_hit_team_allowed` | 期号/团队/默认约 90 天词汇窗口 | acceptance 有 scope/team 场景；跨用户 refs **未做系统测** |
| **FTS** | `search.fts_search` → SQLite FTS5 或 `fts_pg.search_fts` | BM25 / pg_trgm | 黄金 hybrid 题大量依赖，算过 |
| **Vector** | `embeddings.vector_search`；`cosine > 0.05` | 扫 `chunk_embeddings.vector_json`，非 ANN | 有配置才走。黄金报告 mode=hybrid 表示用过向量。生产覆盖度 **未确认**（`/healthz` 的 embeddings_degraded） |
| **Hybrid Merge** | `_hybrid_recall`：FTS + `item_facts.search` + 向量 → `search.merge_hits` | 多 query variant；首路 hits 够就不再 expand | 与上绑定，算过 |
| **Dedup** | `search.hit_dedup_key` / `merge_hits` | body 指纹 / item_id / chunk_id | `test_retriever_hardening`；契约仍写跨源去重不全 |
| **Rerank** | `retriever.rerank_hits` | 标题/正文词面、来源层加分、时效惩罚、seed soft boost。**无模型 rerank** | 单测有；无独立 eval 指标 |
| **Chunk** | `chunk_index` 在 **publish/rebuild** 物化；问时 `hits_to_contexts` ← `ask_contexts_from_hits` | 检索读现成 chunk，不现场切 | 索引正确性靠 publish/reindex 测试，不是 25 题逐步断言 |
| **Group** | `ask_analysis.group_contexts` | `(团队或层, 期号)` 分桶，最多 4 组 | E2E 3 题走过 analysis；无单独 group 验收集 |
| **Evidence** | `_analyze_one_source` 产出 evidence；`_claim_supported` 词面重合；`ask_citation.validate_answer` | 0.35 keep / 0.15 downgrade / 否则 reject | 黄金 e02 等有 `unsupported_claims_bounded`；**无 claim→chunk_id 绑定验收** |
| **Answer** | `_verify_and_compose` 或 `llm.answer_question` | 只用 keep+部分 downgrade 成文，再 citation | E2E 3/3（黄金）。生产 E2E **未跑** |

**不存在的步骤**：LLM query rewrite、学到的 rerank、检索失败后的二次 retrieve、ReAct tool。

结构化短路：`parse_intent` 非空且 `run_structured.ok` → 不走 FTS/向量；`total==0` 则 `direct_answer`，不再分析 LLM。

---

# 6. Agent 能力地图

不要把 workflow 写成 Agent。对照标准定义：

- **Plan**：模型（或等价规划器）为当前目标生成可变更的步骤/路径  
- **ReAct**：观察→行动→再观察的循环，下一步由模型选  
- **Reflection**：根据结果决定重来（重检索/重推理）  
- **Validator**：入口或出口硬校验  
- **Memory**：跨轮可检索的状态（不只是把上次答案再喂进去）

| 能力 | 状态 | 实际对应 | 不是什么 |
|------|------|----------|----------|
| **Plan** | **部分实现（伪 Plan）** | `ask_protocol.STEPS` 写死 DAG；`parse_intent` 规则选 structured/hybrid；`ask_context.route` 只决定 inherit/search_q；`pipeline.PROC+DESIGN` 是 ETL 步骤表 | 没有 `ask_planner`。`AGENT_EVOLUTION.md` 里的 `plan_retrieval` **未实现** |
| **ReAct** | **未实现** | 无 observe-act 循环，无 tool registry | `relation_candidates` 一次算完；`pipeline._run` 线性 |
| **Reflection** | **部分实现（后置 Verify，无闭环）** | `_claim_supported`、`_verify_and_compose`、`ask_citation.validate_answer`、`ask_turn._ground_answer`、`_scrub_user_answer` | 没有「答案差 → 再 retrieve / 再 source」 |
| **Validator** | **部分实现** | 入口：`guard_direct_answer`、`ask_scope`、`ask_rate`、`owner_guard`（周报）。出口：citation、publish_blockers、relation_gate、zone_hard | **没有** retrieval confidence gate（低分 hits 仍进 LLM）。结构化失败会 **静默 fallback hybrid**（`retriever.retrieve`） |
| **Memory** | **部分实现（会话结构化记忆）** | `ask_sessions` / `ask_messages`；`context_refs`（analysis_id、entities、chunk_ids…） | **不是**向量长期记忆。明确禁止把历史 Answer 当记忆。规模也不需要 |

---

# 7. 周报 Agent 单独分析

## 当前周报（如实）

**输入是什么**

- 本期 `sources.text`（挖掘阶段）
- 挖掘后的 `items`（`blocked=0` 且 `merged_into IS NULL`）
- 各团队 `cards.card_json`（approved）
- `build_relation_candidates` 的跨团队候选
- `entities.first_issue == slug` 的首次名（最多 40）
- `stype='T7'` 外媒条目
- 上一期 `published_json` 的 lead + 关系标题（`preview_job._run`）

**如何选择内容**

- 团队：`db.draft_teams_for_issue`（有有效 owner_team 的条目，排除外部媒体）
- 条目：未拦截、未合并
- 关系：代码共现实体 + provenance，**不是模型海选**
- 外媒：只进「外部在聊我们没碰」叙事（提示词约束）

**如何排序**

- 关系候选：`_dedupe_candidates` 按 `len(item_ids)` 降序，Jaccard>0.85 去重
- 要点卡内排序、五块内排序：**交给 LLM + 提示词**，代码无稳定 comparator  
  **未确认**读者页最终渲染是否另有排序

**如何聚合**

- 跨通道：`merge.apply_merge`
- 跨团队：候选列表进 prompt，生成后再 `merge_relations_from_candidates` 锁 teams/sources

**如何生成**

- 每团队一次 `build_team_card`（`00_base_rules` + `card_team`）
- 整期一次 `build_issue_draft`（`00_base_rules` + `issue_draft` + `issue_draft_candidates` + `91_output_issue`）
- `max_tokens=16000` 的 **单次 JSON 调用**

**有没有 evidence 校验**

- 有：provenance、team_has_entity_items、weak 必须人确认、禁用词、⑤区
- 无：逐句回原文、claim→source 行程序对齐（靠提示词「必须带来源」）

## 若升级 Agent，三件套放哪

**不要替换 `run_analysis`，也不要把周报改成开放 ReAct。** 周报已经是可验收的固定 DAG。

| 组件 | 建议挂载点 | 做什么 | 不要做什么 |
|------|------------|--------|------------|
| **Planner** | `preview_job._run` 在 `build_issue_draft` **之前**；或新建 `report_planner.py` 被 `_run` 调用 | 规则输出：做哪些团队卡、关系候选是否够、要不要重抽某 source、外媒是否纳入。confidence 低再 **单次** LLM 分类 | 不要让模型自己决定 25 步顺序 |
| **ReAct** | 仅可选子程序：某个 source 抽取失败 / 拆段 `needs_review` 时 **显式** `retrieve原文→再 extract`（`pipeline._run` 的 extract 循环内） | 有界 1～2 次重试 | 不要对整期草稿开 tool loop |
| **Reflection** | `build_issue_draft` **之后**、`publish_blockers` **之前** | 用现有 `relation_publish_blockers` + 新增「稿内关系是否都在 candidates 里」；失败则只重跑 compose，不重跑全挖掘 | 不要「觉得导语不好就全期重抽」 |

Ask 侧若要 Plan，唯一合理挂点是 **`ask_engine.prepare` 里替换裸 `parse_intent`**，输出 path/set_op 再调现有 `run_structured` / `retrieve`。这是 Planner-lite，仍不是 Agent。

---

# 8. 当前生产状态

## 数据规模

| 环境 | 规模 | 来源 |
|------|------|------|
| 黄金评测库 | 2 期 published（`2026-8-17`, `2026-08-14`）；266 items；244 FTS 行；271 entity_team_facts；510 chunks | `eval/reports/report_20260830_170237.json` |
| 生产 PG | 报告写「多期 published」 | `FINAL_ACCEPTANCE_REPORT.prod.md`。**精确 counts 未确认**（本交接未连生产库） |

## PG / SQLite / 向量

| 组件 | 用途 |
|------|------|
| SQLite `data/mesh.db` | 开发 / 黄金 eval 默认 |
| PostgreSQL 16 | 生产目标；`MESH_DB_URL`；`pg_trgm` 代替 FTS5 |
| `chunk_embeddings` | 同库 JSON 向量；`MESH_EMBED_*` 未配则纯 FTS+facts |
| 无外部向量库 | — |

连接：`db_conn.MeshConnection`，`?` → `%s`，池 `MESH_DB_POOL_MIN/MAX`。

## 部署方式

`mesh/docker-compose.yml`：

- `postgres:16-alpine`（127.0.0.1:5432，volume `mesh_pg_data`）
- `mesh`：uvicorn，宿主机 **8090→8080**，`MESH_JOB_INLINE=0`，默认 2 worker
- `mesh-worker`：`python -m app.job_worker`

也可 `./run.sh` / `run.bat` 单进程 + 默认 inline job。

迁移：`deploy/migrate_to_postgres.py`。备份：`deploy/pg_backup.sh`。  
文档中的主机 `104.250.53.182`（`POSTGRES.md`）：**是否仍是当前线上，未确认**。

## 服务结构

| 进程 | 职责 |
|------|------|
| mesh | FastAPI：读者页、后台、Ask、入队 |
| mesh-worker | pipeline / preview / edm / embed |
| postgres | 主库 |

## 当前线上限制

| 项 | 默认（compose / .env.example） |
|----|--------------------------------|
| Ask LLM 并发 | 每 web worker 2，全局 4 |
| Job LLM | 2 / 全局 2 |
| Ask 速率 | 40/分钟/用户 |
| Ask 排队 | 120s |
| 分析组 | 4×6 |
| 词汇窗口 | 约 90 天 |
| 分析超时 | source 75s / cross 90s / compose 75s |
| 多 worker 答案缓存 | 关闭 |
| SSE 断开 | **前端取消即取消请求**（契约附录；与「后台 persist 可回放」不是同一件事） |
| README 最低机 | 1 核 1G |

生产 08-30 验收缺口：httpx 未装、E2E/SSE 未跑、e20 无 guard。**当前镜像是否已补：未确认。**

---

# 9. 当前问题列表

## P0

### P0-1 结构化意图漏判，hybrid 给出误导答案

- **根因**：`parse_intent` 正则过窄（「跟进了」≠「跟进过」；by_team 语序；「重叠」∉ 交集词）
- **影响**：e08/e10/e21；用户问差集/按团队/关系交集时，拿到 40 条杂命中再经 LLM 复述，**比直接失败更糟**
- **方案**：先扩 3 条正则（有单测）；同时在 `ask_engine.prepare` 增加规则 `plan_retrieval`（path/set_op/confidence），低置信走 hybrid 时 **必须带「未走集合运算」话术**。不要为了 eval 强行 structured

### P0-2 生产 Query Guard 部署状态未知

- **根因**：代码已有 `ask_query_guard`；08-30 生产报告 e20 仍 19 条噪声 hits
- **影响**：乱码问句进 FTS/LLM
- **方案**：部署后用 `run_final_eval.py --corpus prod` 重跑 e20/e25。未部署前不要假设已修

### P0-3 Claim 与证据绑定弱

- **根因**：`_claim_supported` 只做 token overlap；citation 只做实体×团队
- **影响**：分析路径仍可能写出「能过词面、对不上 chunk」的句子
- **方案**：verify 输出强制带 `chunk_id`/`item_id`；UI 按 id 锚原文。契约附录 #2

### P0-4 `ask_analyses` 不在正式 PG schema

- **根因**：只在 `ask_store.ensure_table` / SQLite migrate
- **影响**：空库、迁移脚本、权限审计对这张表不可见；回放依赖「先有人问过一次」
- **方案**：补进 `schema_pg.sql` 与 migrate 清单

## P1

### P1-1 SSE 断开即取消

- **根因**：前端 AbortController；服务端分析在请求线程里跑
- **影响**：刷新/切页丢掉进行中的分析；eval 能测 store 回放，用户体感仍是断了
- **方案**：分析改后台 job（与 preview 同类），SSE 只订阅 `analysis_id`。工作量大，不要和 Planner 捆在一起

### P1-2 生产验收闭环不完整

- **根因**：容器缺 httpx；E2E/SSE 未进生产例行
- **影响**：黄金 22/25 不能外推到线上
- **方案**：`requirements.txt` 加 httpx；`deploy/run_final_acceptance_prod.sh` 进发布检查

### P1-3 向量索引落后

- **根因**：`chunk_embeddings` 少于 `chunk_index` 时 `/healthz` 标 degraded
- **影响**：hybrid 名不副实，退化为 lexical
- **方案**：publish 后自动 `embed_job`；监控两者计数

### P1-4 挖掘 UI 25 步有假完成感

- **根因**：`transcribe` 无 ASR；多步 `defer` 只打标
- **影响**：交接/运营以为「转写、姓名核对」已跑
- **方案**：UI 对 `by=defer` 显示「未在挖掘阶段执行」；或从 ALL_STEPS 拿掉未实现步

## P2

- 读者页引用跳转不稳（契约附录 #4）
- 固定 ~90 天窗口，用户以为「全部历史」（问句需显式「全部历史」）
- SSE 同时发 `status` 与 `step`，前端易重复画进度
- 团队 scope / 跨用户 refs 无系统测试
- `run_eval.py` 断言错误，保留会误导
- 预设飞书推送未完成

## 技术债

- README Schema 1.6.3 vs 代码 1.8.1
- 全库无 FK；关系靠约定
- `intent.type=="error"` 死分支
- 预设 run API 走 `llm.answer_question`，**绕过 analysis**
- 简单路径 `generate_answer` 自己不抢 `llm_slot`，靠调用方包
- `migrate_to_postgres.py` 与 `schema_pg.sql` 表集合不一致
- 向量暴力扫表，当前几百 chunk 能用，期数上去会线性变慢

## 未来规划（仅代码缺口，不是愿景清单）

1. Planner-lite（Ask 路径选择）
2. Retrieval confidence gate
3. Verify v2（claim→chunk）
4. Ask 与 HTTP 生命周期解耦
5. 周报侧：拆段确认体验、关系候选可解释 UI  
**明确不做（现阶段）**：Full ReAct、开放 tool calling、Memory 向量库

---

# 10. 下一阶段路线

## Phase 1 — 把线上验收钉死（目标：可发布）

**目标**：生产与黄金同口径；guard 在线上生效；不再靠过期 prod 报告决策。

**改模块**

- 部署核对：`ask_query_guard.py` 是否在运行中的镜像
- `requirements.txt` / 镜像加 `httpx`
- `schema_pg.sql` + `ask_store.ensure_table` 对齐 `ask_analyses`
- `eval/run_eval.py` 标记废弃或修断言
- `deploy/run_final_acceptance_prod.sh` 纳入发布

**验收标准**

- `run_final_eval.py --corpus golden --e2e --followup --sse`：检索 ≥22/25，E2E 3/3，SSE persist+replay PASS（不比 token 前缀）
- `--corpus prod`：e20/e25 mode=guard；follow-up PASS；SSE 能跑完（不再 httpx 异常）
- `/healthz` 不再长期 `embeddings_degraded`（或文档写明「故意关 embed」）

## Phase 2 — Planner-lite + 结构化问法（目标：差集/交集/按团队不再误导）

**目标**：e08/e10/e21 要么走对 structured，要么 hybrid **明确说没做集合运算**。不引入 ReAct。

**改模块**

- 新建 `mesh/app/ask_planner.py`：`plan_retrieval(q, refs) -> {path, set_op, section, hardware, teams, confidence}`
- 改 `ask_engine.prepare`：用 plan 代替裸 `parse_intent`
- 小改 `qa_structured.parse_intent`（「跟进了」「重叠」、by_team 后置 topic）
- 可选：`retriever.retrieve` 在 structured fallback 时把 `structured_fallback` 暴露给成文

**验收标准**

- 新增单测覆盖三句真实问法
- golden：e08/e10/e21 的 mode **或** 固定 fallback 话术断言通过
- 原 structured 题（e01/e09 等）不回退
- 延迟：prepare 不增加 chat LLM 调用

## Phase 3 — Verify 加深 + 周报可观测（目标：能指到原文）

**目标**：Ask 每条保留 claim 能指到 chunk/item；周报上线闸门可解释。

**改模块**

- `ask_analysis._claim_supported` / `_verify_and_compose`：claim 带 `chunk_id`/`item_id`
- `ask_citation.py`：优先用 id，其次实体×团队
- 读者页 `app.js`：引用跳 `chunk_id`
- 周报：`relation_gate` 错误带回 `item_ids`；控制台展示候选来源
- **不要**在本阶段做 Ask 全异步，除非 Phase 1 SSE 仍阻塞发布

**验收标准**

- E2E 增加「answer 中的主体能在 sources_json.evidence 找到 id」
- `unsupported_claims_bounded` 不放松
- 上线一条含弱关系的草稿必须被 `publish_blockers` 拦住（已有行为，写成回归）
- 仍 **零 ReAct**；周报仍是 `preview_job._run` 固定顺序

---

# 附录 A · 完整架构图

```
                         ┌──────────── 用户 ────────────┐
                         │ 读者页 /{slug}  /archive     │
                         │ 后台 /admin/issue/{slug}     │
                         │ 飞书 OAuth（仅登录）          │
                         └─────────────┬───────────────┘
                                       │ HTTP 同步
                         ┌─────────────▼───────────────┐
                         │  FastAPI  mesh/app/main.py   │
                         │  auth / ask_rate / ask_scope │
                         └──────┬──────────────┬────────┘
                                │              │
              ┌─────────────────▼──┐    ┌──────▼──────────────────┐
              │ ASK（请求内同步）    │    │ 周报 / Job（可异步）        │
              │                    │    │                          │
              │ ask_turn           │    │ ingest → sources         │
              │   └ ask_context    │    │ pipeline._run            │
              │     route 规则     │    │   llm.extract_source ★   │
              │ ask_engine.prepare │    │   owner / zone / merge   │
              │   guard            │    │ preview_job._run         │
              │   parse_intent     │    │   build_team_card ★      │
              │   retriever        │    │   build_issue_draft ★    │
              │     ├ structured   │    │   relation lock          │
              │     └ hybrid       │    │ publish_blockers         │
              │       FTS+facts+vec│    │ db.reindex_issue         │
              │ ask_analysis       │    │ edm_job / embed_job      │
              │   group            │    └──────────┬───────────────┘
              │   source ★ 并行    │               │
              │   cross ★ 可选     │               ▼
              │   verify+compose ★ │    ┌─────────────────────┐
              │ ask_citation       │    │  PostgreSQL / SQLite │
              │ ask_store / log    │    │  issues sources items│
              └─────────┬──────────┘    │  cards entities      │
                        │               │  search_fts          │
                        ▼               │  entity_team_facts   │
              ┌─────────────────┐      │  item_facts*         │
              │ UI SSE/JSON     │      │  chunk_index         │
              │ context_refs    │      │  chunk_embeddings    │
              └─────────────────┘      │  ask_* mesh_jobs     │
                                       └─────────────────────┘

  ★ = chat LLM          虚线 Job = mesh-worker（MESH_JOB_INLINE=0）
  无 ★ 的菱形 = 规则 / SQL / 代码闸门
  图中不存在：Planner 模块、ReAct 环、独立向量库、周报 cron
```

## 附录 B · 关键文件索引

| 路径 | 一句话 |
|------|--------|
| `mesh/app/main.py` | 全部路由；`_run_ask`；`publish_blockers` |
| `mesh/app/ask_engine.py` | 检索编排 `prepare` |
| `mesh/app/ask_analysis.py` | 固定分析流水线 |
| `mesh/app/ask_context.py` | 追问路由与 refs |
| `mesh/app/ask_citation.py` | 句级归属过滤 |
| `mesh/app/ask_query_guard.py` | 入口 nonsense guard |
| `mesh/app/retriever.py` | structured / hybrid |
| `mesh/app/qa_structured.py` | 规则意图 + SQL 集合运算 |
| `mesh/app/search.py` | FTS、merge、contexts |
| `mesh/app/embeddings.py` | 嵌入与暴力向量检索 |
| `mesh/app/pipeline.py` | 挖掘；25 步表含大量 defer |
| `mesh/app/preview_job.py` | 卡 + 草稿 |
| `mesh/app/relation_candidates.py` | 关系候选与 draft bundle |
| `mesh/app/relation_gate.py` | 上线关系闸 |
| `mesh/app/llm.py` | 所有 chat LLM 出口 |
| `mesh/app/db.py` / `schema_pg.sql` | 库 |
| `mesh/app/job_store.py` / `job_worker.py` | 异步任务 |
| `mesh/eval/run_final_eval.py` | 主验收 |
| `mesh/eval/ask_eval_v1.jsonl` | 25 题 |

## 附录 C · 不要从这些文件推断「已有能力」

- `mesh/docs/AGENT_EVOLUTION.md` — 演进设计，Planner 未写
- `mesh/docs/ASK_ANALYSIS_CONTRACT.md` — 契约 + 未关闭附录
- `mesh/app/pipeline.py` 的 `DESIGN` 里 `by="defer"` / `by="model"` 步 — 多数不是独立可验收动作
- `README.md` Schema 1.6.3、飞书 Bot 推送 — 与代码不一致
