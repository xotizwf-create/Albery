#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 /path/to/backup.dump" >&2
  exit 2
fi

backup_path="$1"
if [ ! -f "$backup_path" ]; then
  echo "Backup file not found: $backup_path" >&2
  exit 1
fi

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

echo "Creating pre-restore backup..."
"$APP_DIR/scripts/backup_postgres.sh"

echo "Restoring $backup_path..."
pg_restore --clean --if-exists --no-owner --no-acl --dbname="$DATABASE_URL" "$backup_path"

echo "Applying required migrations..."
"$APP_DIR/.venv/bin/python" "$APP_DIR/scripts/ensure_postgres.py"

echo "Restore completed."
