# GeekPark Mesh · 部署与操作说明

这是一套可直接运行的完整系统：数据库 + 上传/抓取 + AI 抽取 + 周报生成 + 网页发布 + 混合搜索/AI 问答 + EDM 发送。
不需要写代码。按下面步骤做即可。

**Schema 1.6.3** · 混合检索（FTS + 条目事实 + 可选向量）+ 多轮问答 + 交叉结构化查询。

---

## 一、你需要准备的三样东西

1. 一台能上网的服务器（Linux，1 核 1G 即可）或你的电脑（Mac / Windows / Linux）。
2. **一个大模型的 API Key**——AI 抽取、生成周报、AI 问答都靠它。
3. （可选）SMTP 发 EDM；（可选）飞书 OAuth；（可选）**向量嵌入 API**（`MESH_EMBED_*`，启用语义检索）。

---

## 二、最简单的启动方式（三步）

### 方式 A：本机 / 服务器直接跑（需要 Python 3.10 以上）
```
1. 解压代码包，进入目录 mesh/
2. 复制 .env.example 为 .env，填 ANTHROPIC_API_KEY、MESH_SECRET、MESH_ADMIN_PASSWORD
3. Mac / Linux：./run.sh   Windows：run.bat
```
浏览器打开 http://localhost:8080 ，用 `admin` + 密码登录。

### 方式 B：Docker（服务器推荐）
```
1. 填好 .env（MESH_BASE_URL 填正式域名）
2. docker compose up -d --build
```
数据在 `./data/`；备份此目录即备份全部。

---

## 三、默认账号（首次启动自动创建）

| 账号 | 密码 | 角色 |
|---|---|---|
| admin | .env 里的 MESH_ADMIN_PASSWORD | 管理员 |
| biz | mesh-dept | 编辑（演示，团队=商业化团队） |
| viewer | mesh-viewer | 只读 |

**角色说明**（2026-03 起）：
- **owner** — 唯一可确认上线
- **admin** — 用户与提示词管理
- **editor** — 上传、抽取、编辑、草稿、EDM（进后台）
- **viewer** — 只读读者页与 Ask

> **已废弃**：`dept`（团队负责人放行）流程已移除。要点卡 AI 生成即可用，不再逐团队签字。

飞书用户首次登录默认 viewer，管理员在「用户」页提权。

---

## 四、每周操作流程（后台 /admin，六步）

1. **建一期**：期号 = URL（如 `2026-08-21`），起止日期与显示标题。
2. **放来源**：上传 / 粘贴 / 抓取 RSS。选 **T1–T13**（T13=内容中心混合包，按段拆分抽取）与默认团队。
3. **挖掘与脱敏**：点「生成预览」或先跑管线再生成。AI 拆条目、标 ①–⑦ 区；⑤ 区/L3 硬拦。T13 混合包可在来源行改 `owner_team`。
4. **核对要点卡（可选）**：AI 已自动生成各团队要点卡；可改 JSON。草稿含**全部有效条目**（无「未放行团队」概念）。
5. **预览与编辑**：读者页预览 → 就地改标题/正文/卡片。
6. **所有者上线 → EDM**：owner 手打「上线」确认；测试 EDM 后正式发送。

**已上线期重抽条目**：系统自动更新搜索/问答索引（条目快照）；读者页正文需重新上线才变。

---

## 五、页面与功能对照

- **读者页**：五节关系网 + 智能搜索 / AI 问答（多轮、带来源）。
- **搜索**：关键词 + 可选向量语义（需 `MESH_EMBED_*`）。
- **Ask API**：`POST /api/ask`、`/api/ask/stream`；`POST /api/ask/new_session` 新对话。
- **规则提示词**：后台 → 规则提示词（含 `extract_T13.md`）。

---

## 六、.env 主要配置项

| 项 | 说明 |
|---|---|
| ANTHROPIC_API_KEY | 必填。主 LLM。 |
| MESH_SECRET | 必填。会话签名，≥16 字符随机串。 |
| MESH_ADMIN_PASSWORD | 首次 admin 密码。 |
| MESH_BASE_URL | 站点 URL（EDM、飞书回调）。 |
| MESH_LLM_PROVIDER / MESH_LLM_* | 换模型（Anthropic / OpenAI 兼容）。 |
| MESH_EMBED_ENABLED | 默认 1；无 Key 时向量检索自动降级。 |
| MESH_EMBED_API_KEY / MESH_EMBED_BASE_URL / MESH_EMBED_MODEL | 向量嵌入（可与 LLM 同厂商）。 |
| MESH_ASK_RATE_PER_MIN | Ask 速率限制（默认 40/分钟/用户）。 |
| SMTP_* | EDM |
| FEISHU_APP_ID / FEISHU_APP_SECRET | 飞书登录 |

---

## 七、目录结构（1.6.3）

```
mesh/
├─ app/
│  ├─ main.py           路由与流程
│  ├─ db.py             SQLite、快照、索引
│  ├─ pipeline.py       25 步挖掘管线
│  ├─ preview_job.py    一键生成要点卡+草稿
│  ├─ merge.py          跨通道合并
│  ├─ aggregator.py     T13 混合包拆分
│  ├─ item_facts.py     条目事实索引（读 publish 快照）
│  ├─ retriever.py      多路召回 + rerank
│  ├─ ask_turn.py       多轮问答回合
│  ├─ ask_engine.py     问答编排
│  ├─ embeddings.py     向量嵌入
│  ├─ chunk_index.py    统一 chunk 索引
│  ├─ conversation.py   会话历史
│  ├─ presets.py        个性化预设（API；推送待飞书 Bot）
│  ├─ qa_structured.py  交叉结构化问答
│  ├─ llm.py / auth.py / edm.py / ingest.py
│  ├─ prompts/          规则提示词
│  ├─ templates/        issue_console.html 四步后台
│  └─ static/           app.js / console.js
├─ tests/               冒烟脚本
├─ data/                mesh.db + raw/
└─ docker-compose.yml
```

---

## 八、运维 API

| 端点 | 说明 |
|---|---|
| GET /healthz | 探活；含 chunk_index / chunk_embeddings 计数 |
| POST /admin/reindex_search | 全量重建 FTS + 事实表 |
| POST /admin/issue/{slug}/reindex_published | 单期重建（含条目快照） |
| POST /admin/embed_backfill | 回填向量（需 MESH_EMBED_*） |

---

## 九、常见问题

- **chunk_embeddings=0**：配置 `MESH_EMBED_*` 后重启，或调 `POST /admin/embed_backfill`。
- **Ask 429**：调低频率或增大 `MESH_ASK_RATE_PER_MIN`。
- **重抽后 Ask 与页面不一致**：Ask 跟条目快照；页面跟 `published_json`，需 owner 重新上线更新页面。
- **重置**：停服务，删 `data/mesh.db`，重启。

---

## 附录 · 历史变更摘要

- **v2 (2026-08)**：双通道、owner_team、跨通道合并、owner 上线三闸门。
- **v1.6 (2026-03)**：混合 Ask、多轮会话、chunk 索引、条目快照、移除 dept 审批流。
- 后台四屏：① 素材 → ② 挖掘 → ③ 核对 → ④ 上线；管线进度来自 `pipeline.py` 真实状态。
