#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/albery-daily-sync}"
LOG_DIR="${LOG_DIR:-/var/log/albery}"
LOG_FILE="${LOG_FILE:-$LOG_DIR/daily-sync.cron.log}"

mkdir -p "$LOG_DIR"
touch "$LOG_FILE"

cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=Europe/Moscow

0 19 * * * root cd $APP_DIR && ALBERY_LOG_DIR=$LOG_DIR ALBERY_DAILY_SYNC_LOG=$LOG_DIR/daily-sync.log $APP_DIR/.venv/bin/python $APP_DIR/scripts/run_daily_sync.py >> $LOG_FILE 2>&1
EOF

chmod 644 "$CRON_FILE"

echo "Installed daily Albery sync cron: $CRON_FILE"
echo "Cron wrapper log: $LOG_FILE"
echo "Structured sync log: $LOG_DIR/daily-sync.log"
