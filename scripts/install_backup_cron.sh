#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/var/www/albery}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/albery-postgres-backup}"
LOG_FILE="${LOG_FILE:-/var/log/albery-postgres-backup.log}"

cat > "$CRON_FILE" <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

15 3 * * * root APP_DIR=$APP_DIR BACKUP_DIR=/var/backups/albery/postgres RETENTION_DAYS=10 $APP_DIR/scripts/backup_postgres.sh >> $LOG_FILE 2>&1
EOF

chmod 644 "$CRON_FILE"
touch "$LOG_FILE"

echo "Installed daily PostgreSQL backup cron: $CRON_FILE"
echo "Logs: $LOG_FILE"
