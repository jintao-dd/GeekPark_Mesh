#!/bin/sh
set -e
cd /srv/mesh

# Hands CLI Adapter: map existing Feishu app secrets → official CLI env provider.
# No interactive `auth login`; multi-worker / recreate share the same compose env.
if [ -n "${FEISHU_APP_ID:-}" ] && [ -z "${LARKSUITE_CLI_APP_ID:-}" ]; then
  export LARKSUITE_CLI_APP_ID="$FEISHU_APP_ID"
fi
if [ -n "${FEISHU_APP_SECRET:-}" ] && [ -z "${LARKSUITE_CLI_APP_SECRET:-}" ]; then
  export LARKSUITE_CLI_APP_SECRET="$FEISHU_APP_SECRET"
fi
export LARKSUITE_CLI_BRAND="${LARKSUITE_CLI_BRAND:-feishu}"

WORKERS="${MESH_UVICORN_WORKERS:-1}"
if [ "$WORKERS" -gt 1 ] 2>/dev/null; then
  echo "[mesh] uvicorn workers=$WORKERS (PostgreSQL)"
  exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers "$WORKERS"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
