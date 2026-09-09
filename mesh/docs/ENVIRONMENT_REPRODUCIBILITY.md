# Environment / Reproducibility Baseline（已封板）

> **问题：** `docker cp` 热补运行中容器 → recreate 丢代码、与 Git 不对齐、压测/验收不可复现。  
> **正确路径（强制）：** Git commit → Build image → image digest → recreate → **runtime self-check** → smoke。  
> **本文件到此为止：** 不再继续研究部署架构；后续主线是健康 Runtime Performance / Capacity。

## 对齐四方

```
本地代码 (git commit)
        ↓
测试 / harness (同 commit)
        ↓
image: geekpark-mesh:<date>-<sha>
        ↓
tmesh / prod (同 digest + REPRO_STATUS=PASS)
```

## 硬门 1：启动自检

容器启动后写出：

```
REPRO_STATUS=PASS|FAIL
```

路径：`/srv/mesh/data/REPRO_STATUS.json`（及 `eval/reports/`），HTTP：`GET /api/repro/status`。

校验字段：

| 字段 | 说明 |
|------|------|
| git_sha | `MESH_BUILD_GIT_SHA` vs `MESH_EXPECTED_GIT_SHA` / `MESH_GIT_SHA` |
| image_digest / image_tag | ship 注入 / compose |
| prompt_hash | `app/prompts/*.md` 集合 hash |
| ranking_version | v1.4 |
| claim_support_version | v2.4c-2… |
| model | answer/semantic task models |
| vector_enabled / embedding_enabled | 默认 OFF |
| schema_version | `SCHEMA_VERSION` |

**直接 FAIL：**

- `vector_enabled=false` **且** `embedding_calls>0`（启动 prepare 探测）
- `expected_sha != runtime_sha`

`/healthz` 在 `REPRO_STATUS=FAIL` 时返回 503。

## 硬门 2：发布 recreate 验证

固定流程（`deploy/ship_image.ps1`）：

```
git commit
  ↓
build
  ↓
image digest
  ↓
deploy (compose up --force-recreate)
  ↓
runtime self-check (REPRO_STATUS=PASS)
  ↓
smoke
```

recreate 后 manifest 的 commit/tag 必须与本次 build 一致，否则 **不** 报 `SHIP_IMAGE_OK`。

## 必须钉死的字段

| 字段 | 来源 |
|------|------|
| git commit / short | `git rev-parse` + 镜像 `MESH_BUILD_GIT_SHA` |
| image tag / digest / id | `docker image inspect` → `ENV_REPRO_BASELINE` |
| Python | 镜像 `python:3.12-slim` |
| 依赖 | `requirements.txt` + sha256 |
| LLM provider / model | `.env` + `model_for_task`（answer/semantic → Sonnet） |
| Embedding | **默认 Vector OFF**；OFF 时 embedding_calls 必须为 0 |
| Prompt version | baked `prompt_hash` |
| DB schema | `SCHEMA_VERSION` |
| Ranking | **v1.4** |
| Claim Support | **v2.4c-2** |
| Quality baseline | v3.0 Production Baseline |

## 原则（写死）：性能数据必须带 Environment Manifest

任何 Performance / Capacity / Request Profile 报告必须附带：

```
commit: abc123
image: sha256:xxxx
model: claude-sonnet-4-6
vector: OFF
embedding_calls: 0
ranking: v1.4
claim_support: v2.4c-2
```

否则无法判断「P95 8s→15s」是代码变慢还是环境偷换。上一轮无 Manifest / embed 偷跑的 100s 数据**作废**。

## 发布命令

```powershell
cd mesh
.\deploy\ship_image.ps1 -Target tmesh
# tmesh OK 后再
.\deploy\ship_image.ps1 -Target prod
```

## 明确禁止

- 对运行中容器 `docker cp` app 作为发布
- 无 digest / 无 commit / 无 `REPRO_STATUS=PASS` 的「口头同步」
- 无 Environment Manifest 的性能数字当作决策依据

## 遗留例外

`ship_changed_files.ps1`：紧急热修后必须再 `ship_image.ps1` 收口；Agent/Runtime 只走镜像路径。

## 主线（部署基线之后）

```
Environment Baseline ✅
        ↓
健康 Runtime Performance（单请求先做到企业愿意等）
        ↓
Sonnet C=1/2/4/8 → 真实 SLA / Capacity
        ↓
Concurrency / Queue / Timeout
        ↓
Feishu Event → UX / Evidence Card → Canary
```
