#!/bin/bash
# Production source: /usr/local/sbin/vpn-watchdog.sh
# A fresh AmneziaWG handshake is insufficient: policy rules can disappear while the interface
# stays healthy. Validate the effective route first and repair it idempotently.
#
# 16.08.2026 — два урока, оба оплачены простоем:
#
# 1. Свежее рукопожатие НЕ означает, что туннель работает. В тот день awg0 час держал
#    рукопожатия и терял ~90% данных (0 байт принято против 18 КБ отправленных): рукопожатия
#    мелкие и с повторами — они пролезали, полезный трафик нет. Проверка интернета стояла ЗА
#    условием «рукопожатие старше 200 с», поэтому такой отказ вотчдог считал здоровьем.
#    Теперь связность проверяется КАЖДЫЙ прогон, независимо от возраста рукопожатия.
#
# 2. Перезапуск лечит не всё. Когда причина снаружи (блокировка на стороне провайдера),
#    вотчдог перезапускал туннель каждые три минуты примерно двадцать раз подряд — без
#    толку и с обрывом живых запросов на каждом круге. Теперь после нескольких неудачных
#    попыток он ПЕРЕСТАЁТ дёргать туннель и зовёт человека.
set -u

IFACE=${VPN_IFACE:-awg0}
ROUTING_TABLE=${VPN_ROUTING_TABLE:-200}
APPLY_SCRIPT=${VPN_APPLY_SCRIPT:-/usr/local/sbin/vpn-apply.sh}
SPLIT_ROUTES=${VPN_SPLIT_ROUTES:-/usr/local/sbin/vpn-split-routes.sh}
# Проба идёт ЧЕРЕЗ туннель: адрес привязан к интерфейсу, а не к маршруту по умолчанию,
# потому что при раздельном туннеле маршрут по умолчанию ведёт мимо VPN.
PROBE_URL=${VPN_TUNNEL_PROBE_URL:-https://1.1.1.1}
PROBE_TIMEOUT=${VPN_TUNNEL_PROBE_TIMEOUT:-8}
STATE_DIR=${VPN_WATCHDOG_STATE_DIR:-/var/lib/albery}
FAIL_FILE="$STATE_DIR/vpn-watchdog.fails"
ALERT_FILE="$STATE_DIR/vpn-watchdog.alerted"
# После стольких безуспешных перезапусков подряд перестаём дёргать туннель и зовём человека.
MAX_RESTARTS=${VPN_WATCHDOG_MAX_RESTARTS:-4}
ALERT_REPEAT_S=${VPN_WATCHDOG_ALERT_REPEAT_S:-10800}
NOTIFY=${VPN_WATCHDOG_NOTIFY:-/var/www/albery/.venv/bin/python}
NOTIFY_MODULE=${VPN_WATCHDOG_NOTIFY_MODULE:-/var/www/albery/scripts/b24_chat_notify.py}

mkdir -p "$STATE_DIR"
log() { logger -t vpn-watchdog "$*"; }

fails=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
case "$fails" in ''|*[!0-9]*) fails=0 ;; esac

notify_owner() { # $1 = текст
  local now last
  now=$(date +%s)
  last=$(cat "$ALERT_FILE" 2>/dev/null || echo 0)
  case "$last" in ''|*[!0-9]*) last=0 ;; esac
  [ $((now - last)) -lt "$ALERT_REPEAT_S" ] && return 0
  # Битрикс теперь доступен НАПРЯМУЮ, мимо туннеля, — поэтому тревога о мёртвом VPN
  # доходит именно тогда, когда VPN мёртв. В старой модели она ушла бы в тот же туннель.
  if [ -x "$NOTIFY" ] && [ -r "$NOTIFY_MODULE" ]; then
    "$NOTIFY" "$NOTIFY_MODULE" "$1" >/dev/null 2>&1 && echo "$now" > "$ALERT_FILE"
  fi
}

route_policy_ok() {
  # Без 900/901/902 ответы сервера уходят в туннель и коробка пропадает из сети целиком.
  ip rule show | grep -Eq "^[[:space:]]*900:.*sport 22.*lookup main" || return 1
  ip rule show | grep -Eq "^[[:space:]]*901:.*sport 443.*lookup main" || return 1
  ip rule show | grep -Eq "^[[:space:]]*902:.*sport 80.*lookup main" || return 1
  ip rule show | grep -Eq "^[[:space:]]*1000:.*fwmark 0x1.*lookup main" || return 1
  ip rule show | grep -Eq "^[[:space:]]*1001:.*lookup ${ROUTING_TABLE}([[:space:]]|$)" || return 1
  # Раздельный туннель: по умолчанию НАПРЯМУЮ, а в туннель — только allowlist.
  ip route show table "$ROUTING_TABLE" | grep -Eq "^default via .* dev " || return 1
  ip route show table "$ROUTING_TABLE" | grep -Eq "dev ${IFACE}([[:space:]]|$)" || return 1
}

tunnel_carries_data() {
  curl -s -o /dev/null --interface "$IFACE" --max-time "$PROBE_TIMEOUT" "$PROBE_URL"
}

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  log "interface $IFACE missing; restarting tunnel"
  systemctl restart "awg-quick@$IFACE"
  "$APPLY_SCRIPT" >/dev/null 2>&1 || true
  exit 0
fi

if ! route_policy_ok; then
  log "policy routing missing or ineffective; reapplying"
  if "$APPLY_SCRIPT" && route_policy_ok; then
    log "policy routing restored"
  else
    log "policy routing repair failed; restarting tunnel"
    systemctl restart "awg-quick@$IFACE"
    "$APPLY_SCRIPT" >/dev/null 2>&1 || true
    exit 0
  fi
fi

# Связность проверяем ВСЕГДА — именно этой проверки не хватило 16.08.2026.
if tunnel_carries_data; then
  if [ "$fails" -gt 0 ]; then
    log "tunnel carries data again after $fails failed check(s)"
    rm -f "$ALERT_FILE"
  fi
  echo 0 > "$FAIL_FILE"
  # Адреса сервисов за CDN меняются; держим allowlist свежим, пока туннель жив.
  [ -x "$SPLIT_ROUTES" ] && "$SPLIT_ROUTES" >/dev/null 2>&1
  exit 0
fi

fails=$((fails+1))
echo "$fails" > "$FAIL_FILE"

if [ "$fails" -le "$MAX_RESTARTS" ]; then
  log "tunnel up but carries no data (check $fails/$MAX_RESTARTS); restarting"
  systemctl restart "awg-quick@$IFACE"
  "$APPLY_SCRIPT" >/dev/null 2>&1 || true
  exit 0
fi

log "tunnel still dead after $MAX_RESTARTS restarts; stopping restart loop and alerting"
notify_owner "🚨 Albery: VPN-туннель ($IFACE) не пропускает данные уже $fails проверок подряд.
Перезапуски не помогают — причина, скорее всего, снаружи (блокировка на стороне провайдера).
Сервисы за туннелем недоступны; всё остальное (Битрикс, Google, Zoom, GitHub) работает напрямую.
Проверить: awg show $IFACE transfer (принято ≈0 при живом рукопожатии = данные теряются)."
exit 0
