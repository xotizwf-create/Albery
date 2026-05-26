#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
SERVICE_NAME="${SERVICE_NAME:-albery}"
FRONTEND_DIR="${FRONTEND_DIR:-}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

log() {
  printf '\n[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

cd "$APP_DIR"

log "Fetching latest code"
git fetch origin main
git pull --ff-only origin main

log "Preparing Python environment"
if [ ! -x "$VENV_DIR/bin/python" ]; then
  python3 -m venv "$VENV_DIR"
fi
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$APP_DIR/requirements.txt"

log "Applying PostgreSQL schema and migrations"
"$VENV_DIR/bin/python" "$APP_DIR/scripts/ensure_postgres.py"
"$VENV_DIR/bin/python" "$APP_DIR/scripts/update_recommendation_prompt_contracts.py"
"$VENV_DIR/bin/python" "$APP_DIR/scripts/update_owner_daily_prompt_contract.py"

log "Building frontend"
if [ -z "$FRONTEND_DIR" ]; then
  FRONTEND_DIR="$(find "$APP_DIR" -mindepth 1 -maxdepth 2 -name package.json -not -path '*/node_modules/*' -printf '%h\n' | head -n 1)"
fi
if [ -z "$FRONTEND_DIR" ] || [ ! -f "$FRONTEND_DIR/package.json" ]; then
  echo "Frontend package.json was not found" >&2
  exit 1
fi
cd "$FRONTEND_DIR"
if [ -f package-lock.json ]; then
  npm ci
else
  npm install
fi
npm run build

log "Restarting service"
systemctl restart "$SERVICE_NAME"
systemctl --no-pager --full status "$SERVICE_NAME"

log "Local HTTP check"
for attempt in $(seq 1 20); do
  if curl -fsSI http://127.0.0.1:5002 >/dev/null; then
    break
  fi
  if [ "$attempt" -eq 20 ]; then
    journalctl -u "$SERVICE_NAME" -n 80 --no-pager || true
    exit 1
  fi
  sleep 1
done

log "Update completed"
