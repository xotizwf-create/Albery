#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/albery/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

read_env_value() {
  local key="$1"
  local env_file="$2"
  if [ ! -f "$env_file" ]; then
    return 0
  fi
  awk -F= -v key="$key" '
    $1 == key {
      sub(/^[^=]*=/, "")
      gsub(/^[ \t]+|[ \t]+$/, "")
      gsub(/^"|"$/, "")
      gsub(/^'\''|'\''$/, "")
      print
      exit
    }
  ' "$env_file"
}

DATABASE_URL="${DATABASE_URL:-$(read_env_value DATABASE_URL "$APP_DIR/.env")}"

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
