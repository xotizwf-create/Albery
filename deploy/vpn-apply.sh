#!/bin/bash
# Строит политику маршрутизации коробки Albery. Идемпотентен, безопасен для повторного запуска.
# Production target: /usr/local/sbin/vpn-apply.sh
#
# Модель с 16.08.2026 — РАЗДЕЛЬНЫЙ туннель:
#   таблица 200: default НАПРЯМУЮ через шлюз eth0 + adresses из allowlist через awg0.
# Раньше было наоборот (`default dev awg0`), и это стоило часового простоя 16.08.2026: туннель
# сохранил рукопожатия, но перестал пропускать данные — вместе с ним отвалились Битрикс, Google
# и Zoom, которым туннель не нужен вовсе (Битрикс напрямую отвечает за 1,15 с). Теперь простой
# туннеля выключает только те сервисы, которые действительно за ним живут.
#
# Правила 900/901/902 обязательны: они возвращают ОТВЕТЫ сервера (исходящий порт 22/443/80)
# в основную таблицу. Без них ответ на входящее соединение уходит в туннель, обратный путь
# рвётся, и коробка становится недоступна снаружи целиком — сайт, API и SSH разом.
set -u

IFACE=${VPN_IFACE:-awg0}
TABLE=${VPN_ROUTING_TABLE:-200}
SPLIT_ROUTES=${VPN_SPLIT_ROUTES:-/usr/local/sbin/vpn-split-routes.sh}

log() { logger -t vpn-apply "$*"; [ -n "${VPN_APPLY_VERBOSE:-}" ] && echo "$*"; }

GATEWAY=${VPN_DIRECT_GATEWAY:-$(ip route show default 0.0.0.0/0 | awk '/dev/ {for (i=1;i<=NF;i++) if ($i=="via") print $(i+1); exit}')}
DEV=${VPN_DIRECT_IFACE:-$(ip route show default 0.0.0.0/0 | awk '{for (i=1;i<=NF;i++) if ($i=="dev") print $(i+1); exit}')}

if [ -z "${GATEWAY:-}" ] || [ -z "${DEV:-}" ]; then
  log "cannot determine direct gateway/interface; refusing to touch policy routing"
  exit 1
fi

ensure_rule() { # $1 = приоритет, $2… = селектор
  local prio=$1; shift
  if ! ip rule show | grep -Eq "^[[:space:]]*${prio}:"; then
    ip rule add priority "$prio" "$@" || return 1
    log "added ip rule $prio: $*"
  fi
}

# Ответы сервера уходят тем же путём, каким пришёл запрос, — иначе коробка пропадает из сети.
ensure_rule 900 ipproto tcp sport 22 lookup main
ensure_rule 901 ipproto tcp sport 443 lookup main
ensure_rule 902 ipproto tcp sport 80 lookup main
# Пометка 0x1 — аварийный обход туннеля для отдельного процесса.
ensure_rule 1000 fwmark 0x1 lookup main
ensure_rule 1001 lookup "$TABLE"

# Маршрут по умолчанию в таблице 200 — ПРЯМОЙ. Это и есть суть раздельного туннеля:
# что не попало в allowlist, идёт мимо VPN и переживает его простой.
ip route replace default via "$GATEWAY" dev "$DEV" table "$TABLE" \
  || { log "failed to set direct default route in table $TABLE"; exit 1; }

# Локальная сеть, служебный диапазон облака и DNS провайдера — всегда напрямую.
ip route replace "$(ip -4 route show dev "$DEV" scope link | awk 'NR==1{print $1}')" \
  dev "$DEV" scope link table "$TABLE" 2>/dev/null || true
ip route replace 169.254.0.0/16 dev "$DEV" scope link table "$TABLE" 2>/dev/null || true
for dns in $(awk '/^nameserver/ {print $2}' /etc/resolv.conf 2>/dev/null | grep -E '^[0-9.]+$'); do
  case "$dns" in
    127.*) continue ;;
  esac
  ip route replace "$dns" via "$GATEWAY" dev "$DEV" table "$TABLE" 2>/dev/null || true
done
# Endpoint самого туннеля обязан идти напрямую, иначе VPN пытался бы работать через себя.
for endpoint in $(awg show "$IFACE" endpoints 2>/dev/null | awk 'NF>=2{print $2}' | sed -E 's/:[0-9]+$//' | tr -d '[]'); do
  case "$endpoint" in
    *:*) continue ;;  # IPv6 endpoint — отдельная таблица не используется
  esac
  ip route replace "$endpoint" via "$GATEWAY" dev "$DEV" table "$TABLE" 2>/dev/null || true
done

# И только теперь — сам allowlist в туннель.
if [ -x "$SPLIT_ROUTES" ]; then
  "$SPLIT_ROUTES" || log "split-routes sync reported a problem"
else
  log "split routes script $SPLIT_ROUTES is missing; tunnel allowlist not applied"
fi

exit 0
