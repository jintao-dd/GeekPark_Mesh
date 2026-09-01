# PostgreSQL 一步到位迁移

## 架构

```
docker compose
├── postgres:16   ← 持久化 volume mesh_pg_data
└── mesh          ← MESH_DB_URL + 2 uvicorn workers
```

SQLite 仍可用于本地开发（不设置 `MESH_DB_URL`）。

## 生产迁移（104.250.53.182 示例）

### 1. 上传代码并安装依赖

```bash
cd /opt/geekpark-mesh
# scp 新代码后
pip install -r requirements.txt   # 或 rebuild 镜像
```

### 2. 启动 PostgreSQL

```bash
cd mesh
docker compose up -d postgres
# 等待 healthy
docker compose ps
```

### 3. 从现有 SQLite 迁数据

```bash
export MESH_DB=/opt/geekpark-mesh/data/mesh.db
export MESH_DB_URL=postgresql://mesh:mesh@localhost:5432/mesh
python deploy/migrate_to_postgres.py
```

### 4. 切换 .env

```env
MESH_DB_URL=postgresql://mesh:mesh@postgres:5432/mesh
MESH_UVICORN_WORKERS=2
MESH_ASK_LLM_CONCURRENCY=4
```

> 多 worker 时每个进程各有独立 LLM 槽位；2 worker × 4 = 最多 8 路 LLM。可按配额调 `MESH_ASK_LLM_CONCURRENCY=2`。

### 5. 重建并启动

```bash
docker compose up -d --build
docker compose logs -f mesh
curl -s http://127.0.0.1:8090/healthz | python -m json.tool
```

### 6. 验证

- `/healthz` 中 `search_fts_rows` > 0
- 两人同时 AI 问答无 `database is locked`
- EDM 测试发送正常

## 回滚

1. 注释 `.env` 中的 `MESH_DB_URL`
2. `docker compose up -d mesh`（仍用原 `data/mesh.db` 备份）
3. postgres 容器可停：`docker compose stop postgres`

原 SQLite 文件迁移后**不要删**，留作冷备份。

## 新建空库（无 SQLite 可迁）

```bash
export MESH_DB_URL=postgresql://mesh:mesh@localhost:5432/mesh
python -c "from app import db; db.init_db(seed=True)"
```

## 每日备份

```bash
chmod +x deploy/pg_backup.sh
./deploy/pg_backup.sh
# crontab -e 示例（每天 3:15）：
# 15 3 * * * cd /opt/geekpark-mesh && ./deploy/pg_backup.sh >> /opt/geekpark-mesh/backups/backup.log 2>&1
```

备份文件：`backups/mesh-pg-YYYYMMDD-HHMMSS.sql.gz`，默认保留 14 天。

> **勿**对已有数据的 PG 重复运行 `migrate_to_postgres.py`（会 TRUNCATE）。仅首次迁移或加 `--force` 时覆盖。
