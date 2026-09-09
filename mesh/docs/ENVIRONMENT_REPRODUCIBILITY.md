# Environment / Reproducibility Baseline

> **问题：** `docker cp` 热补运行中容器 → recreate 丢代码、与 Git 不对齐、压测/验收不可复现。  
> **正确路径（强制）：** Git commit → Build image → image digest → tmesh/prod recreate。

## 对齐四方

```
本地代码 (git commit)
        ↓
测试 / harness (同 commit)
        ↓
image: geekpark-mesh:<date>-<sha>
        ↓
tmesh / prod (同 digest)
```

## 必须钉死的字段

| 字段 | 来源 |
|------|------|
| git commit / short | `git rev-parse` + 镜像 `MESH_BUILD_GIT_SHA` |
| image tag / digest / id | `docker image inspect` → `ENV_REPRO_BASELINE` |
| Python | 镜像 `python:3.12-slim` |
| Node | n/a（后端镜像不依赖 Node） |
| 依赖 | `requirements.txt` + sha256 |
| LLM provider / model | `.env` + `model_for_task`（answer/semantic/sensitive） |
| Embedding provider / model | `.env`；**默认 Vector OFF** |
| Feature flags | `MESH_EMBED_*` / `MESH_AGENT_USE_LLM` / `MESH_CLAIM_*` … |
| Prompt version | `app/prompts/*.md` 集合 hash（**baked into image**） |
| Gold version | `eval/*gold*.jsonl` sha256 |
| DB schema | `app.db.SCHEMA_VERSION` |
| Ranking | **v1.4** |
| Claim Support | **v2.4c-2** |
| Retrieval | lexical FTS frozen；Vector OFF |
| Quality baseline | v3.0 Production Baseline |

## 发布命令

```powershell
# 1) 提交本地改动
git add … && git commit …

# 2) 镜像发布到 tmesh（禁止再用 ship_changed_files 作为 Agent/Runtime 发布手段）
cd mesh
.\deploy\ship_image.ps1 -Target tmesh

# 3) 验收读基线
# /opt/geekpark-tmesh/eval/reports/ENV_REPRO_BASELINE.tmesh.json
```

本地生成/核对：

```powershell
python eval/emit_repro_baseline.py
```

## 明确禁止

- 对 **运行中** `geekpark-tmesh` / `geekpark-mesh` **`docker cp` app 代码作为发布**
- recreate 后依赖「上次 cp 还在」
- tmesh 用 volume 覆盖 `app/prompts`（已从 staging compose 移除；提示词以镜像为准）
- 无 digest / 无 commit 的「口头同步」

## 遗留例外

`ship_changed_files.ps1` 仅允许：

- 紧急热修 **且** 随后必须补一次 `ship_image.ps1` 收口  
- 或纯宿主脚本 / reports，不进容器 app

Agent / Runtime / Embedding / LLM 相关改动：**只走 `ship_image.ps1`**。
