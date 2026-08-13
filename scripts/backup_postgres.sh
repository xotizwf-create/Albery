#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/albery/postgres}"
RETENTION_DAYS="${RETENTION_DAYS:-10}"

if ! [[ "$RETENTION_DAYS" =~ ^[0-9]+$ ]]; then
  echo "RETENTION_DAYS must be a non-negative integer" >&2
  exit 2
fi

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

exec 9>"$BACKUP_DIR/.backup.lock"
chmod 600 "$BACKUP_DIR/.backup.lock"
if ! flock -n 9; then
  echo "another PostgreSQL backup is already running" >&2
  exit 1
fi

timestamp="$(date '+%Y%m%d_%H%M%S')"
backup_path="$BACKUP_DIR/albery_${timestamp}.dump"
partial_path="$backup_path.partial"
sidecar_path="$backup_path.sha256"
sidecar_partial="$sidecar_path.partial"

cleanup_partial() {
  rm -f -- "$partial_path" "$sidecar_partial"
}
trap cleanup_partial EXIT INT TERM

if [ -e "$backup_path" ] || [ -e "$partial_path" ]; then
  echo "backup target already exists: $backup_path" >&2
  exit 1
fi

pg_dump --format=custom --no-owner --no-acl --file="$partial_path" "$DATABASE_URL"
chmod 600 "$partial_path"
pg_restore --list "$partial_path" >/dev/null

sha256="$(sha256sum "$partial_path" | awk '{print $1}')"
printf '%s  %s\n' "$sha256" "$(basename "$backup_path")" > "$sidecar_partial"
chmod 600 "$sidecar_partial"

mv -- "$partial_path" "$backup_path"
mv -- "$sidecar_partial" "$sidecar_path"

find "$BACKUP_DIR" -type f -name 'albery_*.dump' -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f -name 'albery_*.dump.sha256' -mtime +"$RETENTION_DAYS" -delete
find "$BACKUP_DIR" -type f \( -name 'albery_*.dump.partial' -o -name 'albery_*.dump.sha256.partial' \) -mtime +1 -delete

echo "$backup_path"
