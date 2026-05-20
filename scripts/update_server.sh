#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
SERVICE_NAME="${SERVICE_NAME:-albery}"
FRONTEND_DIR="${FRONTEND_DIR:-$APP_DIR/Интерфейс}"
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

log "Building frontend"
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
curl -fsSI http://127.0.0.1:5002 >/dev/null

log "Update completed"
