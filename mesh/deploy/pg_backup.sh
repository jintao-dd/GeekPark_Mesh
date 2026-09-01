#!/bin/sh
# PostgreSQL 每日备份：pg_dump → gzip，保留 14 天
set -e
cd "$(dirname "$0")/.."
BACKUP_DIR="${MESH_BACKUP_DIR:-./backups}"
KEEP_DAYS="${MESH_BACKUP_KEEP_DAYS:-14}"
mkdir -p "$BACKUP_DIR"
STAMP=$(date +%Y%m%d-%H%M%S)
FILE="$BACKUP_DIR/mesh-pg-$STAMP.sql.gz"
_env_val() {
  grep -E "^$1=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '\r' | sed 's/^["'\'' ]//;s/["'\'' ]$//'
}
USER="$(_env_val POSTGRES_USER)"
DB="$(_env_val POSTGRES_DB)"
USER="${USER:-mesh}"
DB="${DB:-mesh}"
docker compose exec -T postgres pg_dump -U "$USER" "$DB" | gzip > "$FILE"
find "$BACKUP_DIR" -name 'mesh-pg-*.sql.gz' -mtime +"$KEEP_DAYS" -delete 2>/dev/null || true
echo "[mesh-backup] ok $FILE ($(du -h "$FILE" | cut -f1))"
