#!/bin/bash
# Hermes cron pre-script for zoom-to-tasks.
# Lives on prod at /root/.hermes/scripts/zoom_watchdog.sh (source of truth: this file in repo).
# Cheap watchdog: queries Postgres directly for Zoom calls without a saved report.
# If none -> exits silently (no LLM, no Telegram, no Codex burn).
# If new ones found -> runs hermes -z with the prompt template
# from /root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt, substituting $DATE_FROM,
# $DATE_TO and $MISSING via plain bash substitution.

set -euo pipefail
export HOME=/root PATH=/usr/local/bin:/usr/bin:/bin:$PATH

LOCK=/tmp/hermes_zoom_watchdog.lock
exec 9>"$LOCK"
if ! flock -n 9; then
  exit 0
fi

ALBERY_ENV=/var/www/albery/.env
PROMPT_TEMPLATE=/root/.hermes/scripts/hermes_zoom_to_tasks_prompt.txt

DB=$(grep '^DATABASE_URL=' "$ALBERY_ENV" | head -1 | cut -d= -f2- | tr -d '\r\n')
if [ -z "$DB" ]; then
  echo "zoom_watchdog: DATABASE_URL missing"
  exit 1
fi
if [ ! -f "$PROMPT_TEMPLATE" ]; then
  echo "zoom_watchdog: prompt template missing at $PROMPT_TEMPLATE"
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
STATE_DIR=/root/.hermes/state
STATE_FILE="$STATE_DIR/zoom_watchdog.last"
mkdir -p "$STATE_DIR"
NOW=$(date +%s)
COOLDOWN_SECONDS=${ZOOM_WATCHDOG_COOLDOWN_SECONDS:-7200}
if [ -f "$STATE_FILE" ]; then
  read -r LAST_FP LAST_TS < "$STATE_FILE" || true
  if [ "${LAST_FP:-}" = "$FINGERPRINT" ] && [ $((NOW - ${LAST_TS:-0})) -lt "$COOLDOWN_SECONDS" ]; then
    exit 0
  fi
fi
printf '%s %s\n' "$FINGERPRINT" "$NOW" > "$STATE_FILE"

DATE_FROM=$(date -d '2 days ago' +%F)
DATE_TO=$(date +%F)

# Render template: substitute $DATE_FROM, $DATE_TO, $MISSING.
# Use awk to avoid sed escaping pain on multi-line values.
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

hermes -z "$PROMPT"
