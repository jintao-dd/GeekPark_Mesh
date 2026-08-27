#!/usr/bin/env bash
# 热更新前端静态资源到线上容器（不改镜像、不重启）。
# 在 mesh 目录执行：  bash deploy/deploy_static.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DEPLOY_HOST="${MESH_DEPLOY_HOST:-104.250.53.182}"
DEPLOY_PORT="${MESH_DEPLOY_PORT:-22341}"
DEPLOY_USER="${MESH_DEPLOY_USER:-root}"
REMOTE_BASE="${MESH_DEPLOY_REMOTE_BASE:-/opt/geekpark-mesh}"
CONTAINER="${MESH_CONTAINER:-geekpark-mesh}"
BASE_URL="${MESH_BASE_URL:-https://mesh.geekpark.ai}"

SSH=(ssh -p "$DEPLOY_PORT" -o StrictHostKeyChecking=no "${DEPLOY_USER}@${DEPLOY_HOST}")
SCP=(scp -P "$DEPLOY_PORT" -o StrictHostKeyChecking=no)

FILES=(
  "app/static/app.js|$REMOTE_BASE/app/static/app.js|/srv/mesh/app/static/app.js"
  "app/static/console.js|$REMOTE_BASE/app/static/console.js|/srv/mesh/app/static/console.js"
  "app/static/dialog.js|$REMOTE_BASE/app/static/dialog.js|/srv/mesh/app/static/dialog.js"
  "app/static/style.css|$REMOTE_BASE/app/static/style.css|/srv/mesh/app/static/style.css"
  "app/static/console.css|$REMOTE_BASE/app/static/console.css|/srv/mesh/app/static/console.css"
  "app/templates/base.html|$REMOTE_BASE/app/templates/base.html|/srv/mesh/app/templates/base.html"
  "app/templates/issue.html|$REMOTE_BASE/app/templates/issue.html|/srv/mesh/app/templates/issue.html"
  "app/templates/issue_console.html|$REMOTE_BASE/app/templates/issue_console.html|/srv/mesh/app/templates/issue_console.html"
)

echo "==> 发布前检查（本地）"
python scripts/check_frontend.py

echo "==> 上传到服务器 ${DEPLOY_USER}@${DEPLOY_HOST}:${DEPLOY_PORT}"
DOCKER_CP=()
for entry in "${FILES[@]}"; do
  IFS='|' read -r local remote container <<<"$entry"
  if [[ ! -f "$local" ]]; then
    echo "  skip missing $local"
    continue
  fi
  "${SCP[@]}" "$local" "${DEPLOY_USER}@${DEPLOY_HOST}:$remote"
  DOCKER_CP+=("docker cp $remote $CONTAINER:$container")
  echo "  uploaded $local"
done

echo "==> 复制进容器 $CONTAINER"
"${SSH[@]}" "set -e; ${DOCKER_CP[*]}; echo container_ok"

echo "==> 发布后检查（线上）"
python scripts/check_frontend.py --remote "$BASE_URL"

echo ""
echo "DEPLOY_STATIC_OK  已热更新前端；用户需强制刷新 (Ctrl+F5) 或等待缓存过期。"
