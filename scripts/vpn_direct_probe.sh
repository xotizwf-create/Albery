#!/bin/bash
# Замер: какие внешние зависимости Albery РЕАЛЬНО работают напрямую через eth0, а каким
# нужен туннель. Только чтение, ничего не меняет — запускать до правки маршрутов.
#
# Зачем. До 16.08.2026 через VPN шёл ВЕСЬ внешний трафик, поэтому часовой простой туннеля
# отрезал бота от Битрикса, Google и Zoom разом, хотя ни один из них в VPN не нуждается.
# Прежде чем сокращать туннель до нужного минимума, список «нужного» надо ИЗМЕРИТЬ, а не
# угадать: ошибка в другую сторону молча убивает Telegram-агента или голосовые.
#
# Использование:
#   ./scripts/vpn_direct_probe.sh            # обе стороны: напрямую и как сейчас
#   VPN_DIRECT_IFACE=eth0 ./scripts/vpn_direct_probe.sh
set -u

IFACE=${VPN_DIRECT_IFACE:-eth0}
CONNECT_TIMEOUT=${VPN_PROBE_CONNECT_TIMEOUT:-6}
MAX_TIME=${VPN_PROBE_MAX_TIME:-15}

# Проверяем ДОСТИЖИМОСТЬ, а не успех запроса: 401/403/404 от сервиса — это «дошли»,
# и такой ответ считается успехом. Провал — только 000 (соединение не состоялось).
TARGETS=(
  "openai-api|https://api.openai.com/v1/models|мозг агента (Codex/ChatGPT)"
  "openai-chatgpt|https://chatgpt.com/robots.txt|мозг агента, вход Codex CLI"
  "openai-auth|https://auth.openai.com/robots.txt|обновление токена Codex"
  "telegram|https://api.telegram.org|TG-агент @Albery_AI2_Bot, резервные алерты"
  "groq|https://api.groq.com/openai/v1/models|голосовые (Whisper), фильтры лидов"
  "bitrix|https://b24-0xrp3s.bitrix24.ru/|портал: задачи, чаты, CRM — ядро продукта"
  "google-api|https://www.googleapis.com/discovery/v1/apis|Drive, Sheets, Docs"
  "google-oauth|https://oauth2.googleapis.com/|обновление Google-токена"
  "zoom|https://zoom.us/oauth/token|созвоны"
  "github|https://github.com|деплой, git pull на проде"
  "wildberries|https://suppliers-api.wildberries.ru/ping|WB-аналитика"
  "cloudflare-dns|https://1.1.1.1|контрольная точка «интернет вообще есть»"
)

probe() { # $1 = url, $2 = extra curl args
  curl -sS -o /dev/null -w '%{http_code} %{time_total}' \
    --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
    ${2:-} "$1" 2>/dev/null || echo "000 -"
}

printf '%-16s %-14s %-14s %s\n' "СЕРВИС" "НАПРЯМУЮ" "КАК СЕЙЧАС" "ЗАЧЕМ НУЖЕН"
printf '%s\n' "--------------------------------------------------------------------------------"
needs_vpn=()
works_direct=()
for row in "${TARGETS[@]}"; do
  IFS='|' read -r name url purpose <<<"$row"
  direct=$(probe "$url" "--interface $IFACE")
  current=$(probe "$url" "")
  direct_code=${direct%% *}
  printf '%-16s %-14s %-14s %s\n' "$name" "$direct" "$current" "$purpose"
  if [ "$direct_code" = "000" ]; then
    needs_vpn+=("$name")
  else
    works_direct+=("$name")
  fi
done

printf '%s\n' "--------------------------------------------------------------------------------"
echo "Работает НАПРЯМУЮ (туннель не нужен): ${works_direct[*]:-—}"
echo "Напрямую НЕ доступно (оставить в туннеле): ${needs_vpn[*]:-—}"
echo
echo "Напоминание: 000 = соединение не состоялось. Любой HTTP-код (в т.ч. 401/403/404)"
echo "означает, что сервис достижим, и в туннеле он не нуждается."
