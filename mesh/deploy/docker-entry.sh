#!/bin/sh
set -e
cd /srv/mesh
WORKERS="${MESH_UVICORN_WORKERS:-1}"
if [ "$WORKERS" -gt 1 ] 2>/dev/null; then
  echo "[mesh] uvicorn workers=$WORKERS (PostgreSQL)"
  exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --workers "$WORKERS"
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
