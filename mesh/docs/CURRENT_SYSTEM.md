# GeekPark Mesh · 当前系统说明

> 写的是 **2026-08-31 代码里实际在跑的系统**，不是规划。  
> 部署操作看 `README.md`；更细的模块索引看 `ARCHITECTURE_HANDOVER.md`；知识图谱方案看 `KNOWLEDGE_GRAPH_DESIGN.md`（设计，未全面落地）。

---

## 1. 这是什么

Mesh 是极客公园内部用的两件事：

1. **做一期沟通情报周报**：把各部门的会、沟通记录、选题、RSS 放进某一期，抽成条目，生成五块结构的周报，owner 确认后上线，并可发 EDM。
2. **在已上线周报上提问**：问「谁接触了谁、各团队在看什么、两边有没有交集」。答案只能来自已 publish 的语料。

它不是通用知识库，也不是 Agent。Ask 不读草稿、不读未上线素材。

---

## 2. 谁用、从哪进

| 角色 | 入口 | 做什么 |
|------|------|--------|
| editor | `/admin/issue/{slug}` | 建期、上传、挖掘、生成预览、改草稿 |
| owner | 同一控制台第四步 | 确认上线 |
| admin | `/admin/users`、`/prompts`、`/edm` | 账号、提示词、EDM |
| viewer / 飞书登录 | `/{slug}`、`/archive`、读者页 Ask | 读周报、搜索、问答 |

飞书目前只有 **OAuth 登录** 和 **群→团队绑定**。没有飞书 Bot 收消息答问。

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
    ├──────── 周报（人手按钮 + 异步 Job）────────┐
    │  上传 → pipeline 挖掘 → preview 出稿      │
    │  → owner 上线 → 建索引 → 可选发 EDM        │
    │                                            │
    └──────── Ask（请求内同步）─────────────────┐
       Guard → 规则路由 → 检索 → 分析成文        │
                                                 ▼
                              PostgreSQL / SQLite
                         issues / sources / items / cards
                         published_json
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

| 动作 | 接口 | 怎么跑 |
|------|------|--------|
| 上传 / 粘贴 / RSS | `/admin/issue/{slug}/upload\|paste\|fetch` | 同步，写入 `sources` |
| 挖掘 | `POST .../pipeline/start` | 异步 Job `kind=pipeline` |
| 生成预览 | `POST .../preview/start` | 异步 Job `kind=preview` |
| 上线 | `POST .../publish` | 同步，仅 owner |
| 发信 | 上线后 `edm_job.enqueue_auto_send` | 异步 Job `kind=edm` |

### 4.2 挖掘（`pipeline._run`）

对每个有正文的 source 调一次 `llm.extract_source`，写入 `items`，再跑代码步：

- 归属：`owner_guard` / `resolve_item_owner`
- ⑤区、L3：抽取时 `zone_hard.apply_hard_blocks`，`blocked=1`
- 跨通道合并：`merge.apply_merge`（同团队 + 实体重叠 + 文本相似）
- 首次进入：对照 `entities.first_issue`
- 禁用词扫描：`llm.forbidden_hits`
- 旧草稿作废：`db.mark_draft_stale`

`pipeline.py` 里列了 25 步给 UI 看。真正干活的是抽取和上面这些代码步。  
**`transcribe` 没有 ASR**，只统计标题含「录音」的 T6。  
`relation-link` / `compose` / `cite` 等标成 `defer`，挖掘阶段只打「待预览」。

T13 混合包：`aggregator.split_bundle_ex` 按章节切开再分别抽取。

### 4.3 出稿（`preview_job._run`）

1. 确认已有 items、来源都抽完  
2. 再 merge 一次  
3. 每个 `owner_team`（排除外部媒体）一次 `llm.build_team_card` → 表 `cards`  
4. `prepare_draft_bundle`：要点卡 + `build_relation_candidates` + 首次名 + T7 外媒 + 上一期 lead  
5. 一次 `llm.build_issue_draft` 出五块 JSON  
6. `merge_relations_from_candidates` 用代码候选锁 teams/sources，并挂上 evidence  
7. 写入 `issues.draft_json`

周报 JSON 五块（`prompts/91_output_issue.md`）：

`question` / `lead` / `kpis` / **`relations`** / `contacts` / `keywords` / `plans` / `views` / `gaps` / `data_sources`

### 4.4 关系卡怎么来

纯代码发现，LLM 只改写读者文案：

```
items（未拦截、未合并）
  → 丢掉无团队、外部媒体
  → 实体名 → 出现在哪些团队
  → 至少 2 个团队
  → 两队不能只来自同一 (source_id, pointer)   ← provenance
  → relation_candidates
  → LLM 写成 relations[]
  → 按标题对上候选，锁字段，写入 evidence
```

读者页一张关系卡展示：`label`、`title`、`body`、`details[]`、`sources[]`、`teams[]`、`weak`。

生成后还会在 JSON 里带（读者页暂不渲染）：

- `item_ids`
- `evidence[]`：`item_id` / `source_id` / `team` / `snippet` / `quote` / `source_label` / `pointer`
- `relation_type`、`confidence`、`status`、`provenance_ok`

上线闸门 `relation_publish_blockers`：弱关系必须人确认；跨团队必须真有两边条目；强关系不能空 details/sources。

### 4.5 上线之后

`published_json` = 当时的草稿副本。同时：

- 冻条目快照 `published_items_snapshot`
- `register_entities`（只登记名字 + first_issue）
- `reindex_issue`：FTS、`entity_team_facts`、`item_facts`、`chunk_index`
- 可选 SMTP 发 EDM

读者页跟 `published_json`；Ask 跟索引。重抽条目会更新 Ask 索引，页面正文要重新上线才变。

---

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

| 路 | 何时 | 读什么 |
|----|------|--------|
| structured | 问句命中差集/交集/按团队/海外缺口 | `entity_team_facts` |
| hybrid / lexical | 其余，或 structured 失败 | FTS + `item_facts`；配了 embed 再加向量 |

追问只继承上一轮的 `context_refs`（实体、团队、chunk_id、item_id），**不把上一轮答案当证据**。

分析最多 4 组 × 6 条。成文前用词面重合过滤 claim，再 LLM 写成段。  
SSE 会发 step 事件；简单路径会先收完全文再校验，再按块吐出，不是校验后的真 token 流。

---

## 6. 数据长什么样

开发默认 SQLite；生产 PostgreSQL。**没有外键约束。** 没有独立向量库，向量是 `chunk_embeddings.vector_json`。

### 生产对象（表）

| 表 | 作用 |
|----|------|
| `issues` | 一期周报。`draft_json` / `published_json` / 快照 |
| `sources` | 原始材料（T1–T13） |
| `items` | 抽出来的原子条。`owner_team`、`blocked`、`merged_into`、`entities`（JSON 文本） |
| `cards` | 各团队要点卡 JSON |
| `entities` | 名字登记 + `first_issue`。`aliases` 列存在，**基本没人用** |
| `search_fts` | 已上线章节正文 |
| `entity_team_facts` | 从 published_json 拆的「谁×哪队×哪一节」 |
| `item_facts` / `item_entity_facts` | 从条目快照做的检索行 |
| `chunk_index` + `chunk_embeddings` | 统一检索块 + JSON 向量 |
| `ask_sessions` / `ask_messages` | 多轮对话；refs 在 `meta_json` |
| `ask_analyses` | 一次分析的答案、verify、evidence（运行时建表） |
| `mesh_jobs` | 后台任务 |

### 只活在 JSON / 内存里的对象

| 名字 | 在哪 | 说明 |
|------|------|------|
| 周报「Report」 | `issues.published_json` | 五块结构，不是独立表 |
| 关系卡 | `published_json.relations[]` | 展示字段 + 现已附带 evidence |
| 关系候选 | 生成时内存 | `build_relation_candidates` 的输出，不落库 |
| Ask Evidence | `ask_analyses.sources_json` | `{ref, quote}`，组内编号，不绑 item/chunk |
| context_refs | 分析表 + 消息 meta | 追问种子 |

周报和 Ask **没有**统一的 Entity / Relation / Evidence 图。共享的是 `items` 和 `published_json`。上线后单向拆进事实表给 Ask 用。

---

## 7. 现在有 / 没有

**有**

- 人手周报生产、要点卡、五块草稿、上线闸门、EDM  
- 团队名归一（GP → Global Partnership 团队 等）  
- 跨团队关系共现发现 + provenance  
- 关系 JSON 可带 `item_id` / `source_id` / snippet（生成链路已写入；读者页未用来跳转）  
- 已上线语料的 FTS / 结构化差集交集 / 可选向量  
- 乱码拒答、无命中拒答、句级归属过滤  
- 禁用词字段改写、弱关系必须人确认  

**没有**

- 周报自动周更  
- Agent / ReAct / LLM Planner  
- 公司别名合并、人名归一、entity resolution  
- 关系类型体系（提示词有标签库；代码只区分两三种）  
- 关系按价值打分排序  
- 人工改稿回流到下一期规则  
- 跨期关系 id（只有 `first_issue` 和上一期 lead 进 prompt）  
- 点回原文的统一 Evidence 层  
- 飞书群问答、录音转写  

---

## 8. 部署与限制

| 项 | 现状 |
|----|------|
| 生产 | `docker compose`：PG16 + mesh + mesh-worker |
| 本地 | `./run.sh` / `run.bat`，SQLite |
| Ask 并发 | compose 默认每 web worker 2，全局 4 |
| 分析规模 | 最多 4 组 × 6 条 |
| 默认检索窗 | 约 90 天 |
| 速率 | 40 问 / 分钟 / 用户 |
| Schema | 代码 `1.8.1`；README 仍写 1.6.3（文档过期） |

黄金评测（2026-08-30）：检索 22/25，E2E 3/3。失败题是 structured 意图漏判（e08/e10/e21），hybrid 会给出容易误导的命中。生产同口径是否已重跑：**未确认**。

---

## 9. 关键文件

| 路径 | 职责 |
|------|------|
| `app/main.py` | 路由、上线闸门、Ask 入口 |
| `app/pipeline.py` | 挖掘 |
| `app/preview_job.py` | 要点卡 + 草稿 |
| `app/relation_candidates.py` | 关系候选与 evidence 挂载 |
| `app/relation_gate.py` | 上线前关系检查 |
| `app/llm.py` | 抽取 / 要点卡 / 周报 / 问答的 LLM 出口 |
| `app/ask_engine.py` | 检索编排 |
| `app/ask_analysis.py` | 多源分析成文 |
| `app/retriever.py` / `qa_structured.py` | hybrid / 结构化查询 |
| `app/db.py` / `schema_pg.sql` | 库 |
| `app/job_worker.py` | 异步任务 |
| `eval/run_final_eval.py` | 25 题验收 |

LLM 提示词在 `app/prompts/`：`extract_T*.md`、`card_team.md`、`issue_draft.md`、`issue_draft_candidates.md`、`91_output_issue.md`、`qa.md`。

---

## 10. 和其它文档的关系

| 文件 | 用途 |
|------|------|
| 本文件 | **当前系统怎么跑** |
| `README.md` | 怎么部署、怎么每周点按钮 |
| `ARCHITECTURE_HANDOVER.md` | 模块级交接、RAG 逐步对照 |
| `ASK_ANALYSIS_CONTRACT.md` | Ask SSE / context_refs 契约 |
| `KNOWLEDGE_GRAPH_DESIGN.md` | 关系资产层的设计（未当主链路用） |
| `AGENT_EVOLUTION.md` | Agent 化讨论（未实现） |
