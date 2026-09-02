# GeekPark Mesh · 全流程详解

> **口径：2026-09-02 当前代码**（Relation 两阶段 + `decision_tier` + Publish/Embedding 解耦）。  
> 总览见 `CURRENT_SYSTEM.md`；本文按操作顺序展开：**接口 / 提示词 / 数据库 / LLM / 限制 / 索引与向量**。

---

## 0. 总览（你要的那条链）

```
建期 → 材料 → 挖掘 → Preview → 审校 → Publish
  → FTS / Facts / Chunk（同步）→ Embedding（异步）→ EDM（异步/人工）→ Ask
```

| 阶段 | 触发 | 同步/异步 | 主模块 |
|------|------|-----------|--------|
| 建期 | 人工表单 | 同步 | `main.issue_new` |
| 材料 | 上传/粘贴/RSS | 同步 | `ingest` / `aggregator` |
| 挖掘 | 「生成预览」或「重新执行」 | Job `pipeline` | `pipeline._run` |
| Preview | 挖掘后自动 / 直接点 | Job `preview` | `preview_job._run` |
| 审校 | 控制台 / 读者预览编辑 | 同步 API | `main` `/api/edit` 等 |
| Publish | owner「确认上线」 | **同步**写库+索引 | `main.publish` → `db.reindex_issue` |
| Embedding | publish 后自动 | **异步** Job `embed` | `embed_job` / `chunk_index` |
| EDM | publish 后自动或人工 | Job `edm` | `edm` / `edm_job` |
| Ask | 读者提问 | 请求内同步 | `ask_engine` / `ask_analysis` |

后台任务：`mesh_jobs`（`job_kind` + `job_key=slug`），由 web 内联线程或 `mesh-worker` 执行。

---

## 1. 建期

### 人做什么
`/admin/issues` → 新建：填 slug / 起止日 / 期号文案 / version。

### 接口
`POST /admin/issue/new`（需 `editor`）

### 数据库
```sql
INSERT INTO issues(slug, date_start, date_end, period_label, version, status, updated_at)
VALUES(..., 'draft', ...);
```
此时 **没有** `draft_json` / `published_json` / embedding 列（空或默认）。

### 限制
- `date_start > date_end` → 拒绝  
- slug 重复 → 拒绝  
- 缺省：结束日默认今天；slug 可默认成结束日字符串  

---

## 2. 材料（放入 sources）

### 人做什么
控制台 Step1：上传文件 / 粘贴正文 / 拉 RSS。

### 接口

| 动作 | 方法 |
|------|------|
| 上传 | `POST /admin/issue/{slug}/upload` |
| 粘贴 | `POST /admin/issue/{slug}/paste` |
| RSS | `POST /admin/issue/{slug}/fetch`（`which=all|cn|en|ext`） |
| 改元数据 | `POST /api/source_meta` |
| 删源 | `POST /admin/source/{sid}/delete`（连带删 items，并 `mark_draft_stale`） |

### 类型 T1–T13（`ingest.SOURCE_TYPES`）

| 码 | 含义 | 默认团队倾向 |
|----|------|----------------|
| T1 | 编辑部沟通记录 | 编辑部 |
| T2 | 编辑部选题表 | 编辑部 |
| T3 | 硅谷 BD | 硅谷 BD 团队 |
| T4 | 私域朋友圈·群聊 | 社群 |
| T5 | 已发布内容 / 站内 RSS | 英文站 / 编辑部 |
| T6 | 例会 / 周报 / 转写 | 各团队 |
| T7 | 外部媒体 RSS | 外部媒体 |
| T8 | 主体名单 | 投资团队 |
| T9 | 活动档案 | 品牌创意 |
| T10 | GP 工作周报 | Global Partnership |
| T11 | 视频号周数据 | 视频号 |
| T12 | 音频播客 | 音频播客 |
| T13 | 内容中心·数据聚合（混合包） | 会按章节拆成多源再抽 |

上传解析：`.docx/.txt/.md/.csv/.json/.ics/.html` → `ingest.read_upload`。  
混合包：`aggregator.is_mixed_source` → `split_bundle_ex` → **多条** `sources`（`meta.split`）。

### 数据库 `sources` 关键列

| 列 | 含义 |
|----|------|
| `stype` / `team` / `title` / `filename` / `raw_path` / `text` | 类型与正文 |
| `meta` | JSON：体积、推断、split、feed 错误等 |
| `extracted` | 0=未挖；挖完变 1 |
| `channel` | `manual` / `aggregator` |

**本阶段不调 LLM。** 不写 items。

---

## 3. 挖掘（Pipeline）

### 人怎么触发
- 点「生成预览」且「还没 items / 源变脏」→ 先挖再自动 Preview（`console.js` `autoPreviewAfterMining`）  
- 或「重新执行」→ `runMining`

### 接口
| | |
|--|--|
| 开始 | `POST /admin/issue/{slug}/pipeline/start?force=` |
| 轮询 | `GET /admin/issue/{slug}/pipeline/status` |
| 单源补挖 | `POST /admin/source/{sid}/extract` |
| 全量补挖 | `POST /admin/issue/{slug}/extract_all` |

Job：`mesh_jobs.job_kind='pipeline'`。

### 实际干活顺序（`pipeline._run`）

UI 有约 25 步标签；**真正干活**的是：

1. **intake**：读有正文的 sources  
2. **extract（LLM，按源）** → 删旧 items → 插新 items → `extracted=1`  
3. **归属统计 / ⑤区计数**（硬规则多半已在抽取时打上）  
4. **`merge.apply_merge`**：同 `owner_team` 下实体重叠 + 文本相似 → `merged_into`  
5. **lint**：`llm.forbidden_hits` 扫禁用词（统计/提示）  
6. **`db.mark_draft_stale`**：旧草稿 `_stale=true`，不能直接上线  

关系相关步骤在挖掘里标 **`defer`（待 Preview）**。

### 抽取 LLM

**函数：** `llm.extract_source(stype, team, title, text, …)`

**拼进 system 的提示词：**
1. `prompts/00_base_rules.md`  
2. `prompts/05_owner_attrib.md`  
3. `prompts/extract_T{1..13}.md`（按 stype）  
4. 可选团队叠层：T3→`team_svbd.md`，T10→`team_gp.md`，T11→`team_video.md`，T12→`team_podcast.md`  
5. `prompts/90_output_items.md`（输出 schema）

**调用：** `call_json_compliant(..., max_tokens=8000)` → OpenAI 兼容 `chat/completions`（`MESH_LLM_*`）。  
并发：`ask_concurrency.llm_slot(pool="job")`。

**T13：** 通常先代码拆段，再对每段用对应 T1–T12 提示词抽；整包 T13 提示词主要用于禁止「一把梭」。

### 抽取后的代码限制（重要）

| 限制 | 模块 | 行为 |
|------|------|------|
| ⑤区 / L3 | `zone_hard` + 模型 zone/level | `items.blocked=1`，永不进卡/关系 |
| 归属冲突 | `owner_guard` | 同 `(source_id,pointer)` 只能一个 owner；冲突则 block |
| 归属优先级 | `attribution` | manual > segment > source > **LLM 仅 hint**（不能单独成为可上线 owner） |
| 非法团队 | `INVALID_OWNER_TEAMS` | 「内容中心·数据聚合」「其他」等不能当最终 owner |
| 合并 | `merge` | 仅同 owner_team；实体交叠≥0.5 且文本相似≥0.62 |

### 数据库 `items`（每条）

写入字段含：`zone, level, kind, text, entities, roles, signals, source_label(s), pointer, blocked, owner_team, channel, owner_provenance, llm_owner_team_hint`。  
新实体名 → `entities(name, kind, first_issue)`（`ON CONFLICT DO NOTHING`）。

---

## 4. Preview（生成预览）

### 人怎么触发
- 已挖完：点「生成预览」→ 直接 Preview  
- 刚挖完：pipeline done 后前端自动 `preview/start`

### 接口
| | |
|--|--|
| 开始 | `POST /admin/issue/{slug}/preview/start?force=` |
| 轮询 | `GET /admin/issue/{slug}/preview/status` |
| 旧入口 | `POST .../prepare_preview`（同样进 Job） |

Job：`mesh_jobs.job_kind='preview'`。

### `preview_job._run` 逐步

#### 4.0 闸门
- 无 items → 失败「请先完成挖掘与脱敏」  
- 有正文但 `extracted=0` 的源 → 失败  

#### 4.1 merge
再跑一次 `merge.apply_merge`。

#### 4.2 要点卡（每团队 1 次 LLM）
- 团队 = `DISTINCT owner_team`（排除外部媒体、blocked、已 merge）  
- **提示词：** `00_base_rules` + `card_team.md`  
- **调用：** `llm.build_team_card`，`max_tokens=4000`  
- **库：** `cards` upsert，`status='approved'`，`reviewer='mesh-auto'`  
- UI 文案：`要点卡 i/N · {团队}`

#### 4.3 组包 `prepare_draft_bundle`
| 键 | 内容 |
|----|------|
| `team_cards` | 已批准要点卡 |
| `relation_candidates` | `build_relation_candidates(items)`：共现≥2 团队 + 路由 `→` 一方卡 |
| `item_rows` | 可用 items |
| `external_items` | T7 |
| `first_names` | 本期首次实体（最多约 40） |
| 另 | 上一期 published 的 lead + 关系标题作 `prev_summary` |

同时 `mark_draft_stale`（再标一次）。

#### 4.4 周报壳（1 次大 LLM）← 易 524
- **函数：** `llm.build_issue_draft`  
- **提示词：** `00_base_rules` + `issue_draft.md` + `issue_draft_candidates.md` + `91_output_issue.md`  
- **max_tokens：** 16000  
- **硬限制（写在 user 指令里）：`relations` 必须为 `[]`**  
- 产出：`question / lead / kpis / contacts / keywords / plans / views / gaps / data_sources…`  
- UI：`正在生成周报草稿…`  
- Provider 对 **HTTP 524** 会重试（Cloudflare origin timeout）

#### 4.5 关系两阶段（Decision → Gate → Writer）

入口：`merge_relations_from_candidates` → `build_relations_two_phase`。

```
候选 + candidate_id(c1…)
    │
    ▼
① Decision LLM
    提示词: 00_base_rules + issue_relation_decisions.md
    输出: decision(keep/skip), label, relation_type, decision_tier,
          reason, evidence_refs[]
    max_tokens: 8000；缺 candidate_id 会重试（RELATION_DECISIONS_RETRIES）
    禁止写: title/body/details/teams/sources
    │
    ▼
② Evidence Gate（纯代码）apply_evidence_gate
    - 校验 label ∈ 合法集
    - evidence_refs → 拼 evidence[]（snippet/team/source_label…）
    - 跨团队 provenance；单边关系可放宽到 1 队
    - decision_tier 空则 normalize_decision_tier(label)
    - 锁死 RelationObject（含 decision_tier / relation_type）
    - keep 但过不了闸 → skip（记 audit）
    │
    ▼
③ Writer LLM  relation_writer
    提示词: 00_base_rules + issue_relation_writer.md
    只写: title, body, details
    LOCKED_FIELDS 含 label/teams/sources/evidence/decision_tier/relation_type…
    │
    ▼
④ 后处理
    去弱重复、narrative verify、attach_reader_flags
    filter_draft_relations、丢掉无 evidence
    split → _relations_reader / _relations_backlog
    verify_issue_draft
    finalize_decision_audit → _relation_decision_audit
```

**`decision_tier`：** `strong` | `parallel` | `watch` | `skip`。  
**读者投影：** `strong` → Reader；`parallel`/`watch` → draft backlog；`skip` → 无。  
`reader_visible` 为派生字段。正式入口：`build_published_projection(draft)`。

#### 4.6 落库
```sql
UPDATE issues SET draft_json=?, published_json=?, updated_at=? WHERE id=?;
-- draft_json: 全量（含 backlog + audit）
-- published_json: build_published_projection(draft) → 仅 strong relations，剥离内部字段
INSERT INTO edits(..., 'prepare_preview', ...);
```
另：`register_entities`、`reindex_issue`（此时若仍 draft，索引侧对 Ask **不开放**；见 Publish）。  
**`status` 仍为 `draft`。**  
完成 → 前端跳 `/{slug}` 预览编辑。

---

## 5. 审校

### 人做什么
- 控制台：看要点卡、弱关系、上线闸门列表  
- 读者预览页 `?preview=1`：改标题正文、增删卡  

### 接口

| 接口 | 写什么 |
|------|--------|
| `POST /api/edit` | **只改** `draft_json`（path 指向字段）；关系变更会重算 KPI + reader flags |
| `POST /api/add_card` / `add_item` | 往 draft 结构里加 |
| `POST .../save_draft` | 整包覆盖 draft |
| `GET/POST .../weak_relations*` | 弱关系列表 / confirm\|delete |
| `POST .../confirm_split` | 清 split 待审 |
| `POST .../request_publish` | 非 owner：只记「请求上线」审计 |

### 上线闸门 `publish_blockers`（控制台 + Publish 再验）
1. 无 sources  
2. 无 items  
3. 无 cards  
4. 空 `owner_team` / 非法 owner  
5. 草稿未就绪（`_stale` 或不可渲染）→「请先重新生成周报草稿」  
6. 混合包 `needs_review` 未确认  
7. `relation_gate.issue_publish_blockers`（证据、双团队、details/sources…）  
8. `attribution_publish_blockers`  

注意：UI「弱关系」常看 `needs_review`；GET weak API 看 `weak` 标志——两者不完全同一集合。

---

## 6. Publish（确认上线）

### 人做什么
owner：`POST /admin/issue/{slug}/publish`（控制台 / 列表 / 预览页）。

### 同步（同一请求内，commit 前）

1. 再跑一遍 `publish_blockers`，有则拒绝  
2. `draft_json` 去掉 `_stale`，刷新 `_relations_reader` / `_relations_backlog`  
3. `published = build_published_projection(draft)`（仅 strong + 剥离内部字段）  
4. ```sql
   UPDATE issues SET
     published_json=?,   -- projection
     draft_json=?,       -- 全量草稿（含 parallel/watch backlog）
     status='published',
     published_at=?, updated_at=?,
     period_label/date_* 按上线日刷新
   ```
5. `db.register_entities(published)`  
6. `db.snapshot_published_items` → `published_items_snapshot`  
7. **`db.reindex_issue`**（下一节）  
8. `INSERT versions(snapshot_json=published)`  
9. `INSERT edits(target='publish')`  
10. **COMMIT**

### commit 之后（不挡响应）
- `embed_job.ensure_for_slug(slug, by="publish")`  
- `edm_job.enqueue_auto_send(slug, …)`  
- 清 Ask 进程内缓存  

**Reader 双保险：** `issue.html` / `load_issue(published_only)` 再过滤 `decision_tier == strong`。

**撤回上线：** `status='draft'` + `reindex_issue` 清公开索引；`published_json` 可仍留着。

---

## 7. FTS / Facts / Chunk（Publish 同步索引）

全部在 `db.reindex_issue`（及被它调用的模块）里，**上线请求内同步完成**。

| 产物 | 表 | 数据从哪来 | 干什么 |
|------|-----|------------|--------|
| **FTS** | `search_fts` | `published_json` 各节（关系/接触/关键词/计划/看法/缺口…） | 中文分词 `toks`，Ask/搜索词面召回 |
| **Item facts** | `item_facts` + `item_entity_facts` + FTS | 优先 `published_items_snapshot` | 条目级检索、团队过滤 |
| **Entity×Team** | `entity_team_facts` | 从 published_json 物化 | 结构化差集/交集问句 |
| **Chunk** | `chunk_index` | section 层←FTS 行；item 层←item_facts | 统一检索块；为向量做挂载点 |
| **Embedding 状态** | `issues.embedding_*` | 统计 chunk vs 已嵌入数 | 标 pending，**不在此填向量** |

未 published / 空 published_json：删该期 FTS 等公开语料，Ask 看不到。

---

## 8. Embedding（异步向量）

### 何时入队
| 触发 | by |
|------|-----|
| Publish 成功后 | `publish` |
| `POST .../reindex_published` | `reindex` |
| 进程启动扫 `issues_needing_embedding` | `startup` |
| 管理台 backfill | `global` |

### 配置（`embeddings.py`）
- `MESH_EMBED_BASE_URL` / `MESH_EMBED_API_KEY` / `MESH_EMBED_MODEL`（默认 `text-embedding-3-small`）  
- `MESH_EMBED_BATCH`（默认 32）  
- 可用 `MESH_EMBED_ENABLED=0` 关闭 → status=`skipped`

### Job 做什么（`embed_job` → `chunk_index.embed_missing_for_slug`）
1. `embedding_status=pending|running`  
2. 找出当前 model 下缺 `chunk_embeddings` 的 chunk  
3. 对 `title\nbody`[:4000] 调 embedding API  
4. 写入 `chunk_embeddings(chunk_id, model, dim, vector_json)`  
5. 刷新 `embedding_done/total` → `completed` / `partial` / `failed`  

**不是独立向量库**：向量 JSON 存在 PG/SQLite 表里；检索时 **暴力余弦**（`embeddings.vector_search`），可按 slug/team/日期过滤。

**限制：** Publish **不等** 向量完成；Ask 向量未就绪时仍可用 FTS + item_facts。

---

## 9. EDM

### 自动
Publish 后 `edm_job.enqueue_auto_send`：
- 读 settings：`edm_auto_send:{issue_id}` 或默认 `edm_auto_send_default`（默认开）  
- 必须已 published + 有默认 To（`edm_default_to` / `EDM_DEFAULT_TO`）  
- 正文：`published_json` → `edm.render_edm`（`edm_email_inline.html`）  
- 主题：`build_edm_subject`（可 LLM 亮点，失败走规则）  
- SMTP：`SMTP_HOST/PORT/USER/PASSWORD/FROM`  
- 结果：`mail_log`  

### 人工
- `/admin/edm`：默认收件人、自动开关、测试/正式发送  
- `/admin/issue/{slug}/edm` + `.../edm/send`  
- 测试信可对 draft；正式信必须 published  

### 限制
自动关闭 / 无收件人 → Job 跳过并可能记 log；正式发送失败有限次重试。

---

## 10. Ask

### 接口
| | |
|--|--|
| 问答 | `POST /api/ask` |
| 流式 | `POST /api/ask/stream`（SSE） |
| 检索 | `GET /api/search` |
| 分析回放 | `GET /api/ask/analysis/{id}` |

权限：`viewer+`。**只读已上线语料**（走索引，不读 draft）。

### 请求内流程
```
限流 ask_rate（默认 40/分）
  → ask_scope（期号/团队/日期窗）
  → ask_engine.prepare
       · 乱码等 guard → 可直接拒答
       · 规则意图 → structured（entity_team_facts）或 hybrid
       · hybrid = search_fts + item_facts +（可选）chunk 向量 → 合并重排
  → 无命中：固定「未找到」，不调 LLM
  → 有命中：
       · 默认分析模式（MESH_ASK_ANALYSIS）：ask_analysis 六步
       · 或简单路径：llm.answer_question + 引用校验
  → 写入 ask_sessions / ask_messages / ask_analyses / ask_log
```

### 分析六步（`ask_analysis`）
`route → retrieve → group → source* → cross? → verify`  
- 最多约 4 组 × 6 条（`MESH_ANALYSIS_MAX_*`）  
- source/cross/verify 用**内联** system 文案（不只靠 qa.md）  
- 追问只继承 `context_refs`，**不把上轮答案当证据**

### 简单问答提示词
`00_base_rules` + `qa.md`：必须可溯源、不编造、不碰 L3/⑤区。

### 限制一览
| 项 | 默认 |
|----|------|
| 速率 | 40 问/分钟/用户或 IP |
| Ask LLM 并发 | 每 worker 2，全局 4 |
| Job LLM 池 | 与 Ask 分离（`MESH_JOB_LLM_*`） |
| 分析规模 | 4 组 × 6 条 |
| 引用 | `ask_citation` 删无「实体×团队」依据的句子 |

---

## 11. 提示词文件总表

| 文件 | 阶段 |
|------|------|
| `00_base_rules.md` | 几乎所有 LLM |
| `05_owner_attrib.md` | 挖掘抽取 |
| `extract_T1.md` … `extract_T13.md` | 挖掘 |
| `team_svbd/gp/video/podcast.md` | 挖掘叠层 |
| `90_output_items.md` | 抽取输出格式 |
| `card_team.md` | Preview 要点卡 |
| `issue_draft.md` / `issue_draft_candidates.md` | Preview 周报壳 |
| `91_output_issue.md` | 周报 JSON schema |
| `issue_relation_decisions.md` | Relation Decision |
| `issue_relation_writer.md` | Relation Writer |
| `qa.md` | Ask 简单成文 |

---

## 12. 关键表总表

| 表 | 阶段写入/使用 |
|----|----------------|
| `issues` | 全流程；draft/published/embedding_* |
| `sources` | 材料；`extracted` |
| `items` | 挖掘 |
| `cards` | Preview |
| `entities` | 挖掘/Preview 登记名 |
| `edits` / `versions` | 审计与版本 |
| `mesh_jobs` | pipeline/preview/embed/edm |
| `search_fts` | Publish 索引 |
| `item_facts*` / `entity_team_facts` | Publish 索引 |
| `chunk_index` / `chunk_embeddings` | Chunk + 异步向量 |
| `mail_log` | EDM |
| `ask_*` / `ask_rate_hits` | Ask |

---

## 13. 一句话对照「向量库 / 索引」

- **没有**独立向量库产品；向量是表 `chunk_embeddings.vector_json`。  
- **索引**在 Publish 同步建：FTS（章节）+ Facts（条目/实体团队）+ Chunk（挂载点）。  
- **向量**在 Publish 后异步补齐，服务 Ask 的 semantic 召回；补不齐也不挡上线。  

---

## 14. 与其它文档

| 文件 | 用途 |
|------|------|
| **本文** `FULL_PIPELINE_DETAIL.md` | 端到端逐步：接口/提示词/库/限制/索引 |
| `CURRENT_SYSTEM.md` | 当前系统说明（含 Preview 详细节） |
| `ARCHITECTURE_HANDOVER.md` | 模块交接 |
| `ASK_ANALYSIS_CONTRACT.md` | Ask SSE / refs 契约 |
| `README.md` | 部署与每周操作 |
