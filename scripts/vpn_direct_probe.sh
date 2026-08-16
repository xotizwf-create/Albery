#!/bin/bash
# Замер: какие внешние зависимости Albery РЕАЛЬНО работают напрямую через eth0, а каким
# нужен туннель. Только чтение, ничего не меняет — запускать до правки маршрутов.
#
# Зачем. До 16.08.2026 через VPN шёл ВЕСЬ внешний трафик, поэтому часовой простой туннеля
# отрезал бота от Битрикса, Google и Zoom разом, хотя ни один из них в VPN не нуждается.
# Прежде чем сокращать туннель до нужного минимума, список «нужного» надо ИЗМЕРИТЬ, а не
# угадать: ошибка в другую сторону молча убивает Telegram-агента или голосовые.
#
# ВАЖНО (урок 16.08.2026). Достижимость и пригодность — разные вещи. api.openai.com с
# российского адреса отдаёт вполне живой HTTP 403 с телом unsupported_country_region_territory,
# а api.groq.com — 403 Forbidden. Прежняя версия замера считала «любой HTTP-код = туннель не
# нужен» и потому советовала вынести из туннеля мозг агента и голосовые. Теперь ответ
# признаётся прямым только если сервис не только ответил, но и НЕ отказал по географии.
#
# Использование:
#   ./scripts/vpn_direct_probe.sh            # обе стороны: напрямую и как сейчас
#   VPN_DIRECT_IFACE=eth0 ./scripts/vpn_direct_probe.sh
set -u

IFACE=${VPN_DIRECT_IFACE:-eth0}
CONNECT_TIMEOUT=${VPN_PROBE_CONNECT_TIMEOUT:-6}
MAX_TIME=${VPN_PROBE_MAX_TIME:-15}

# Признаки отказа по географии в теле ответа. Список намеренно узкий: он должен ловить
# «страна не поддерживается», а не любое слово «forbidden» в произвольной выдаче.
GEO_MARKERS='unsupported_country|country, region, or territory|not available in your|not supported in your|region is not supported|"Forbidden"|access denied for your|restricted in your (country|region)'

# Коды, означающие «сервис нас обслуживает» (403 сюда НЕ входит — см. урок выше).
USABLE_CODES='^(200|201|204|301|302|303|307|308|400|401|404|405|429)$'

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

# Печатает "код|время|тело". Тело нужно, чтобы отличить геоблок от рабочего ответа.
probe() { # $1 = url, $2 = extra curl args
  local out tail_line code elapsed body
  out=$(curl -sS --connect-timeout "$CONNECT_TIMEOUT" --max-time "$MAX_TIME" \
        -w '\n%{http_code} %{time_total}' ${2:-} "$1" 2>/dev/null)
  if [ -z "$out" ]; then
    printf '000|-|'
    return
  fi
  tail_line=$(printf '%s' "$out" | tail -n 1)
  code=$(printf '%s' "$tail_line" | awk '{print $1}')
  elapsed=$(printf '%s' "$tail_line" | awk '{print $2}')
  body=$(printf '%s' "$out" | sed '$d' | tr -d '\r\n' | cut -c1-400)
  printf '%s|%s|%s' "${code:-000}" "${elapsed:--}" "$body"
}

# Решение по одному сервису: DIRECT (идёт напрямую) | GEO (геоблок) | DEAD (нет связи).
classify() { # $1 = direct_code, $2 = direct_body, $3 = current_code
  local dcode="$1" dbody="$2" ccode="$3"
  [ "$dcode" = "000" ] && { echo DEAD; return; }
  if printf '%s' "$dbody" | grep -Eqi "$GEO_MARKERS"; then
    echo GEO
    return
  fi
  # 403 напрямую при рабочем коде через текущий маршрут — тоже отказ по географии,
  # даже если тело нам ничего не сказало.
  if [ "$dcode" = "403" ] && printf '%s' "$ccode" | grep -Eq "$USABLE_CODES"; then
    echo GEO
    return
  fi
  echo DIRECT
}

printf '%-16s %-14s %-14s %-9s %s\n' "СЕРВИС" "НАПРЯМУЮ" "КАК СЕЙЧАС" "ВЕРДИКТ" "ЗАЧЕМ НУЖЕН"
printf '%s\n' "--------------------------------------------------------------------------------------"
needs_vpn=()
works_direct=()
geo_blocked=()
for row in "${TARGETS[@]}"; do
  IFS='|' read -r name url purpose <<<"$row"
  IFS='|' read -r direct_code direct_time direct_body <<<"$(probe "$url" "--interface $IFACE")"
  IFS='|' read -r current_code current_time _ <<<"$(probe "$url" "")"

  case "$(classify "$direct_code" "$direct_body" "$current_code")" in
    DEAD) verdict="нет связи"; needs_vpn+=("$name") ;;
    GEO)  verdict="ГЕОБЛОК";   needs_vpn+=("$name"); geo_blocked+=("$name") ;;
    *)    verdict="напрямую";  works_direct+=("$name") ;;
  esac

  printf '%-16s %-14s %-14s %-9s %s\n' \
    "$name" "$direct_code $direct_time" "$current_code $current_time" "$verdict" "$purpose"
done

printf '%s\n' "--------------------------------------------------------------------------------------"
echo "Работает НАПРЯМУЮ (туннель не нужен): ${works_direct[*]:-—}"
echo "Оставить в туннеле: ${needs_vpn[*]:-—}"
[ ${#geo_blocked[@]} -gt 0 ] && echo "  из них отказ по географии (HTTP есть, но обслуживать не будут): ${geo_blocked[*]}"
echo
echo "Как читать: 000 = соединение не состоялось. HTTP-код сам по себе НЕ означает, что"
echo "сервисом можно пользоваться: 403 с телом про страну/регион — это отказ по географии,"
echo "и такой сервис обязан остаться в туннеле (проверено 16.08.2026 на OpenAI и Groq)."
