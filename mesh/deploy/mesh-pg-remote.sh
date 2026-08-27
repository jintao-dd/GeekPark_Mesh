#!/usr/bin/env bash
# 在服务器 /opt/geekpark-mesh 执行（由 deploy_postgres.ps1 触发）
set -euo pipefail
cd /opt/geekpark-mesh

cp data/mesh.db "data/mesh.db.bak-$(date +%Y%m%d-%H%M%S)" || true
tar -xzf mesh-pg-deploy.tgz
rm -f mesh-pg-deploy.tgz

if ! grep -q '^POSTGRES_PASSWORD=' .env 2>/dev/null; then
  PG_PASS=$(openssl rand -base64 24 | tr -dc 'a-zA-Z0-9' | head -c 24)
  echo "POSTGRES_USER=mesh" >> .env
  echo "POSTGRES_DB=mesh" >> .env
  echo "POSTGRES_PASSWORD=$PG_PASS" >> .env
  echo "==> 已写入 POSTGRES_PASSWORD（见 .env）"
fi
grep -q '^MESH_UVICORN_WORKERS=' .env || echo 'MESH_UVICORN_WORKERS=2' >> .env
grep -q '^MESH_ASK_LLM_CONCURRENCY=' .env || echo 'MESH_ASK_LLM_CONCURRENCY=2' >> .env
POSTGRES_USER=$(grep '^POSTGRES_USER=' .env | tail -1 | cut -d= -f2-)
POSTGRES_PASSWORD=$(grep '^POSTGRES_PASSWORD=' .env | tail -1 | cut -d= -f2-)
POSTGRES_DB=$(grep '^POSTGRES_DB=' .env | tail -1 | cut -d= -f2-)
export POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB

echo "==> 启动 PostgreSQL"
docker compose up -d postgres
for i in $(seq 1 30); do
  if docker compose exec -T postgres pg_isready -U mesh -d mesh >/dev/null 2>&1; then break; fi
  sleep 2
done
docker compose exec -T postgres pg_isready -U mesh -d mesh

echo "==> 构建 mesh 镜像"
docker compose build mesh

echo "==> 迁移 SQLite -> PostgreSQL"
docker compose run --rm --no-deps \
  -e MESH_DB=/srv/mesh/data/mesh.db \
  -e MESH_DB_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}" \
  mesh python deploy/migrate_to_postgres.py

echo "==> 重启 mesh（PG 模式）"
docker compose up -d mesh
sleep 4
docker compose ps
curl -sS http://127.0.0.1:8090/healthz | head -c 500 || true
echo ""
docker logs geekpark-mesh --tail 20
echo "DEPLOY_POSTGRES_OK"
