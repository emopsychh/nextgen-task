#!/usr/bin/env bash
# Safe Postgres backup for nextgen-task production compose.
# Usage (on the VPS, from the repo root):
#   ./scripts/backup-db.sh
# Cron example (daily 03:15 UTC):
#   15 3 * * * cd /opt/nextgen-task/nextgen-task && ./scripts/backup-db.sh >> /var/log/nextgen-backup.log 2>&1

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.prod.yml --env-file .env.production)
BACKUP_DIR="${BACKUP_DIR:-/root/backups}"
KEEP_DAYS="${KEEP_DAYS:-14}"
STAMP="$(date -u +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/nextgen-task-${STAMP}.sql.gz"

mkdir -p "$BACKUP_DIR"

PG_USER="$("${COMPOSE[@]}" exec -T postgres printenv POSTGRES_USER | tr -d '\r')"
PG_DB="$("${COMPOSE[@]}" exec -T postgres printenv POSTGRES_DB | tr -d '\r')"

echo "Backing up ${PG_DB} as ${PG_USER} → ${OUT}"
"${COMPOSE[@]}" exec -T postgres pg_dump -U "$PG_USER" "$PG_DB" | gzip > "$OUT"

SIZE="$(stat -c%s "$OUT" 2>/dev/null || stat -f%z "$OUT")"
if [[ "${SIZE}" -lt 1000 ]]; then
  echo "ERROR: backup too small (${SIZE} bytes)" >&2
  exit 1
fi

echo "OK ${OUT} (${SIZE} bytes)"
find "$BACKUP_DIR" -name 'nextgen-task-*.sql.gz' -type f -mtime +"${KEEP_DAYS}" -print -delete || true
