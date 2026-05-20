#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/albery/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

if [ -f "$APP_DIR/.env" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$APP_DIR/.env"
  set +a
fi

if [ -z "${DATABASE_URL:-}" ]; then
  echo "DATABASE_URL is not set" >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

timestamp="$(date '+%Y%m%d_%H%M%S')"
backup_path="$BACKUP_DIR/albery_${timestamp}.dump"

pg_dump --format=custom --no-owner --no-acl --file="$backup_path" "$DATABASE_URL"
chmod 600 "$backup_path"

find "$BACKUP_DIR" -type f -name 'albery_*.dump' -mtime +"$RETENTION_DAYS" -delete

echo "$backup_path"
