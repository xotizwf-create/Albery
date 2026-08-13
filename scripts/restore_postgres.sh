#!/usr/bin/env bash
set -Eeuo pipefail

# This helper is intentionally limited to an isolated drill database. Restoring over the
# live `albery` database requires the reviewed DR runbook, a maintenance window and an
# explicit operator confirmation; it must never happen from a one-argument command.
if [ "$#" -ne 2 ]; then
  echo "Usage: $0 /path/to/backup.dump albery_restore_<drill_name>" >&2
  exit 2
fi

backup_path="$1"
target_database="$2"
pg_host="${PGHOST:-/var/run/postgresql}"
pg_port="${PGPORT:-5432}"

if ! [[ "$pg_port" =~ ^[0-9]+$ ]] || [ "$pg_port" -lt 1 ] || [ "$pg_port" -gt 65535 ]; then
  echo "Invalid PostgreSQL port: $pg_port" >&2
  exit 2
fi

if [ ! -f "$backup_path" ]; then
  echo "Backup file not found: $backup_path" >&2
  exit 1
fi
if ! [[ "$target_database" =~ ^albery_restore_[a-zA-Z0-9_]+$ ]]; then
  echo "Refusing unsafe target; use an isolated albery_restore_* database" >&2
  exit 2
fi
if [ "$target_database" = "albery" ]; then
  echo "Refusing to restore over the production database" >&2
  exit 2
fi

pg_restore --list "$backup_path" >/dev/null

if sudo -u postgres psql --host="$pg_host" --port="$pg_port" -d postgres -tAc \
  "SELECT 1 FROM pg_database WHERE datname = '$target_database'" | grep -qx 1; then
  echo "Refusing to overwrite existing database: $target_database" >&2
  exit 1
fi

cleanup_failed_restore() {
  if [ "${restore_complete:-0}" != "1" ]; then
    sudo -u postgres dropdb --host="$pg_host" --port="$pg_port" --if-exists -- "$target_database"
  fi
}
trap cleanup_failed_restore EXIT INT TERM

sudo -u postgres createdb --host="$pg_host" --port="$pg_port" -- "$target_database"
sudo -u postgres pg_restore \
  --exit-on-error --no-owner --no-acl --jobs="${RESTORE_JOBS:-2}" \
  --host="$pg_host" --port="$pg_port" --dbname="$target_database" "$backup_path"

sudo -u postgres psql --host="$pg_host" --port="$pg_port" -d "$target_database" -v ON_ERROR_STOP=1 -tAc \
  "SELECT count(*) FROM information_schema.tables WHERE table_schema='public' AND table_type='BASE TABLE'" \
  | grep -Eq '^[1-9][0-9]*$'

amcheck_bin="$(command -v pg_amcheck || true)"
if [ -z "$amcheck_bin" ] && command -v pg_config >/dev/null 2>&1; then
  candidate="$(pg_config --bindir)/pg_amcheck"
  if [ -x "$candidate" ]; then
    amcheck_bin="$candidate"
  fi
fi
if [ -n "$amcheck_bin" ]; then
  sudo -u postgres "$amcheck_bin" --host="$pg_host" --port="$pg_port" \
    --install-missing --database="$target_database"
fi

restore_complete=1
trap - EXIT INT TERM
echo "Isolated restore completed: $target_database"
echo "Review it, then remove it explicitly: sudo -u postgres dropdb -- $target_database"
