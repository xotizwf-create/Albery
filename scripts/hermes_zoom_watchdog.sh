#!/usr/bin/env bash
# Hermes cron pre-script for zoom-to-tasks.
# Cheap watchdog: queries Postgres directly for Zoom calls without a saved report.
# If none -> exits silently (no LLM, no Telegram, no Codex burn).
# If work exists -> starts a detached guarded worker so report generation is not killed by
# the short no-agent cron timeout.
set -euo pipefail
export HOME=/root PATH=/usr/local/bin:/usr/bin:/bin:$PATH

LOCK=/tmp/hermes_zoom_watchdog.lock
PROCESS_LOCK=/tmp/hermes_zoom_watchdog_processing.lock
LOG_DIR=/root/.hermes/logs/zoom_watchdog
STATE_DIR=/root/.hermes/state
STATE_FILE="$STATE_DIR/zoom_watchdog.last"
ALBERY_ENV=/var/www/albery/.env
PROMPT_TEMPLATE=/root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt
mkdir -p "$LOG_DIR" "$STATE_DIR"

exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

DB=$(grep '^DATABASE_URL=' "$ALBERY_ENV" | head -1 | cut -d= -f2- | tr -d '\r\n')
if [ -z "$DB" ]; then
  echo "Не нашёл подключение к базе Albery для проверки Zoom"
  exit 1
fi
if [ ! -f "$PROMPT_TEMPLATE" ]; then
  echo "Не нашёл шаблон обработки Zoom Albery"
  exit 1
fi

MISSING=$(psql "$DB" -At -F $'\t' -c "
select id::text, call_date::text, coalesce(topic, technical_topic, 'Без темы')
from zoom_calls
where call_date >= (current_date - interval '2 days')::date
  and coalesce(analytical_note, '') = ''
  and (
    coalesce(transcript_text, '') <> ''
    or exists (select 1 from zoom_call_transcript_segments s where s.call_id = zoom_calls.id)
  )
order by call_date, start_time_msk;
")
if [ -z "$MISSING" ]; then
  exit 0
fi

FINGERPRINT=$(printf '%s' "$MISSING" | sha256sum | awk '{print $1}')
NOW=$(date +%s)
COOLDOWN_SECONDS=${ZOOM_WATCHDOG_COOLDOWN_SECONDS:-900}
if [ -f "$STATE_FILE" ]; then
  read -r LAST_FP LAST_TS < "$STATE_FILE" || true
  if [ "${LAST_FP:-}" = "$FINGERPRINT" ] && [ $((NOW - ${LAST_TS:-0})) -lt "$COOLDOWN_SECONDS" ]; then
    exit 0
  fi
fi

# If a previous worker is still building/saving reports, do not start a duplicate.
if ! flock -n "$PROCESS_LOCK" true; then
  exit 0
fi

DATE_FROM=$(date -d '2 days ago' +%F)
DATE_TO=$(date +%F)
RUN_ID=$(date +%Y%m%d_%H%M%S)
LOG_FILE="$LOG_DIR/run_${RUN_ID}.log"

(
  exec 8>"$PROCESS_LOCK"
  flock -n 8 || exit 0

  PROMPT=$(MISSING="$MISSING" DATE_FROM="$DATE_FROM" DATE_TO="$DATE_TO" \
    awk '{
      gsub(/\$DATE_FROM/, ENVIRON["DATE_FROM"]);
      gsub(/\$DATE_TO/,   ENVIRON["DATE_TO"]);
      if (index($0, "$MISSING")) {
        n = split(ENVIRON["MISSING"], lines, "\n");
        for (i = 1; i <= n; i++) print lines[i];
      } else {
        print;
      }
    }' "$PROMPT_TEMPLATE")

  if OUTPUT=$(hermes -z "$PROMPT" 2>&1); then
    printf '%s %s\n' "$FINGERPRINT" "$(date +%s)" > "$STATE_FILE"
    if [ -n "${OUTPUT:-}" ]; then
      /var/www/albery/.venv/bin/python /var/www/albery/scripts/b24_chat_notify.py "$OUTPUT" >>"$LOG_FILE" 2>&1 || true
    fi
  else
    {
      echo "Автоматическая обработка Zoom Albery не завершилась. Следующая проверка попробует снова."
      echo "$OUTPUT" | tail -40
    } >>"$LOG_FILE"
    /var/www/albery/.venv/bin/python /var/www/albery/scripts/b24_chat_notify.py "Не смог автоматически сформировать отчёт по новому Zoom-созвону Albery. Следующая проверка попробует снова; задачи в Битрикс не ставил." >>"$LOG_FILE" 2>&1 || true
    exit 1
  fi
) >>"$LOG_FILE" 2>&1 &

# Detached worker delivers the final Telegram message itself. Keep cron silent and fast.
exit 0
