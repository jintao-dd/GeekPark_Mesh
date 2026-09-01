#!/usr/bin/env bash
# 在新加坡服务器部署 tmesh 测试环境（与 mesh 生产隔离）
# 用法（服务器 root）：
#   bash deploy/setup_tmesh.sh
#   bash deploy/setup_tmesh.sh --clone-prod-db   # 从生产 PG 克隆数据（可选）
set -euo pipefail

PROD_DIR="${MESH_PROD_DIR:-/opt/geekpark-mesh}"
STAGING_DIR="${MESH_STAGING_DIR:-/opt/geekpark-tmesh}"
DOMAIN="${TMESH_DOMAIN:-tmesh.geekpark.net}"
PORT="${TMESH_PORT:-8091}"
NGINX_VHOST="/www/server/panel/vhost/nginx/${DOMAIN}.conf"
CLONE_DB=0
[[ "${1:-}" == "--clone-prod-db" ]] && CLONE_DB=1

echo "==> Staging dir: ${STAGING_DIR}"
mkdir -p "${STAGING_DIR}/data/raw" "${STAGING_DIR}/app/prompts"

if [[ ! -d "${PROD_DIR}/app" ]]; then
  echo "ERROR: prod not found at ${PROD_DIR}" >&2
  exit 1
fi

echo "==> Sync app + deploy from prod (code baseline)"
rsync -a --delete \
  "${PROD_DIR}/app/" "${STAGING_DIR}/app/" \
  --exclude '__pycache__' --exclude '*.pyc'
rsync -a "${PROD_DIR}/deploy/" "${STAGING_DIR}/deploy/"
for f in Dockerfile requirements.txt docker-compose.staging.yml docker-entry.sh run.sh; do
  if [[ -f "${PROD_DIR}/${f}" ]]; then
    cp -a "${PROD_DIR}/${f}" "${STAGING_DIR}/${f}"
  fi
done
# docker-entry lives under deploy on prod
if [[ -f "${PROD_DIR}/deploy/docker-entry.sh" ]]; then
  cp -a "${PROD_DIR}/deploy/docker-entry.sh" "${STAGING_DIR}/deploy/docker-entry.sh"
fi

echo "==> Write .env for tmesh"
if [[ -f "${PROD_DIR}/.env" ]]; then
  cp "${PROD_DIR}/.env" "${STAGING_DIR}/.env"
else
  cp "${PROD_DIR}/.env.example" "${STAGING_DIR}/.env"
fi
# 测试库独立库名；站点 URL 指向 tmesh
sed -i "s|^MESH_BASE_URL=.*|MESH_BASE_URL=https://${DOMAIN}|" "${STAGING_DIR}/.env"
sed -i "s|^MESH_LOGO_URL=.*|MESH_LOGO_URL=https://${DOMAIN}/static/assets/mesh_logo.png|" "${STAGING_DIR}/.env"
grep -q '^POSTGRES_DB=' "${STAGING_DIR}/.env" && \
  sed -i 's/^POSTGRES_DB=.*/POSTGRES_DB=tmesh/' "${STAGING_DIR}/.env" || \
  echo 'POSTGRES_DB=tmesh' >> "${STAGING_DIR}/.env"
grep -q '^MESH_ALLOW_PROD_PUBLISH=' "${STAGING_DIR}/.env" && \
  sed -i 's/^MESH_ALLOW_PROD_PUBLISH=.*/MESH_ALLOW_PROD_PUBLISH=1/' "${STAGING_DIR}/.env" || \
  echo 'MESH_ALLOW_PROD_PUBLISH=1' >> "${STAGING_DIR}/.env"
# 标记 staging（若应用读取）
grep -q '^MESH_ENV=' "${STAGING_DIR}/.env" && \
  sed -i 's/^MESH_ENV=.*/MESH_ENV=staging/' "${STAGING_DIR}/.env" || \
  echo 'MESH_ENV=staging' >> "${STAGING_DIR}/.env"

cd "${STAGING_DIR}"
echo "==> docker compose up (port ${PORT})"
docker compose -f docker-compose.staging.yml up -d --build

echo "==> Wait for tmesh postgres"
for i in $(seq 1 30); do
  docker exec geekpark-tmesh-pg pg_isready -U mesh -d tmesh >/dev/null 2>&1 && break
  sleep 2
done

if [[ "${CLONE_DB}" == "1" ]]; then
  echo "==> Clone prod DB mesh -> tmesh (staging only)"
  docker exec geekpark-mesh-pg pg_dump -U mesh -d mesh --no-owner --clean --if-exists \
    | docker exec -i geekpark-tmesh-pg psql -U mesh -d tmesh -v ON_ERROR_STOP=1
  docker restart geekpark-tmesh geekpark-tmesh-worker
fi

echo "==> Nginx vhost ${DOMAIN} -> 127.0.0.1:${PORT}"
mkdir -p "/www/wwwroot/${DOMAIN}"
cat > "${NGINX_VHOST}" <<EOF
server
{
    listen 80;
    server_name ${DOMAIN};
    root /www/wwwroot/${DOMAIN};

    location /.well-known {
        allow all;
    }

    location /api/ask/stream {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        chunked_transfer_encoding on;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        client_max_body_size 8m;
    }

    location / {
        proxy_pass http://127.0.0.1:${PORT};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
        client_max_body_size 512m;
    }

    access_log /www/wwwlogs/${DOMAIN}.log;
    error_log /www/wwwlogs/${DOMAIN}.error.log;
}
EOF

nginx -t && nginx -s reload

echo ""
echo "DONE tmesh staging"
echo "  URL (after DNS A record): http://${DOMAIN}"
echo "  Direct: http://$(curl -s ifconfig.me 2>/dev/null || echo SERVER_IP):${PORT}"
echo "  Dir: ${STAGING_DIR}"
echo "  DB: postgres/tmesh (isolated volume tmesh_pg_data)"
echo ""
echo "DNS: add A record ${DOMAIN} -> server IP"
echo "HTTPS: apply cert in 宝塔 for ${DOMAIN}, then mirror mesh.geekpark.ai SSL block"
echo "Clone prod data later: bash deploy/setup_tmesh.sh --clone-prod-db"
