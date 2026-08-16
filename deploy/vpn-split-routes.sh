#!/bin/bash
# Синхронизирует маршруты «через туннель» для списка из vpn-split-domains.conf.
# Production target: /usr/local/sbin/vpn-split-routes.sh
#
# Модель (с 16.08.2026): в таблице 200 маршрут по умолчанию ведёт НАПРЯМУЮ через eth0, а в
# туннель уходят только адреса из allowlist. Обратная модель («всё через VPN») стоила часового
# простоя: туннель потерял данные — и вместе с ним отвалились Битрикс, Google и Zoom, которым
# туннель не нужен вовсе.
#
# Адреса сервисов за CDN меняются, поэтому список пересобирается по таймеру. Скрипт
# идемпотентен: добавляет недостающее, убирает своё устаревшее и НИКОГДА не трогает маршруты,
# которых сам не создавал (список своих — в STATE_FILE).
set -u

IFACE=${VPN_IFACE:-awg0}
TABLE=${VPN_ROUTING_TABLE:-200}
CONF=${VPN_SPLIT_CONF:-/etc/albery/vpn-split-domains.conf}
STATE_FILE=${VPN_SPLIT_STATE:-/var/lib/albery/vpn-split-routes.state}

log() { logger -t vpn-split-routes "$*"; [ -n "${VPN_SPLIT_VERBOSE:-}" ] && echo "$*"; }

if [ ! -r "$CONF" ]; then
  log "config $CONF is missing; no split routes applied"
  exit 0
fi

# Туннеля нет — синхронизировать нечего. Это НЕ ошибка: маршрут по умолчанию в таблице 200
# ведёт напрямую, поэтому всё, кроме allowlist, продолжает работать без туннеля.
if ! ip link show "$IFACE" >/dev/null 2>&1; then
  log "interface $IFACE is down; leaving split routes untouched"
  exit 0
fi

mkdir -p "$(dirname "$STATE_FILE")"
touch "$STATE_FILE"

# Состояние хранится с ВЛАДЕЛЬЦЕМ записи: «owner<TAB>target». Владелец — это строка конфига
# (host api.openai.com / net 1.2.3.0/24), а не просто адрес. Без владельца временно молчащий
# резолвер выглядел бы как «домен больше не нужен», и маршруты к мозгу агента снесло бы на
# ровном месте: DNS моргнул — OpenAI отвалился.
desired=""

add_target() { # $1 = владелец, $2 = CIDR или IP
  local target=$2
  case "$target" in
    */*) ;;
    *) target="$target/32" ;;
  esac
  desired="${desired}${1}"$'\t'"${target}"$'\n'
}

carry_over() { # $1 = владелец, у которого резолв не дал адресов
  local kept=0
  while IFS=$'\t' read -r owner target; do
    if [ "$owner" = "$1" ] && [ -n "$target" ]; then
      desired="${desired}${owner}"$'\t'"${target}"$'\n'
      kept=$((kept+1))
    fi
  done < "$STATE_FILE"
  [ "$kept" -gt 0 ] && log "resolver returned nothing for $1; keeping $kept known address(es)"
}

while IFS= read -r raw; do
  line=${raw%%#*}
  line=$(echo "$line" | tr -d '\r' | xargs 2>/dev/null || true)
  [ -z "$line" ] && continue
  kind=${line%% *}
  value=${line#* }
  case "$kind" in
    host)
      # Резолвим системным резолвером: DNS провайдера маршрутизируется напрямую, поэтому
      # имена разрешаются даже при мёртвом туннеле — иначе список нечем было бы обновить.
      resolved=$(getent ahostsv4 "$value" 2>/dev/null | awk '{print $1}' | sort -u)
      if [ -z "$resolved" ]; then
        carry_over "host $value"
      else
        while IFS= read -r addr; do
          [ -n "$addr" ] && add_target "host $value" "$addr"
        done <<<"$resolved"
      fi
      ;;
    net)
      add_target "net $value" "$value"
      ;;
    *)
      log "unknown directive in $CONF: $line"
      ;;
  esac
done < "$CONF"

desired=$(printf '%s' "$desired" | sed '/^$/d' | sort -u)

if [ -z "$desired" ]; then
  log "allowlist is empty; keeping existing routes untouched"
  exit 0
fi

added=0
while IFS=$'\t' read -r owner target; do
  [ -z "${target:-}" ] && continue
  if ip route replace "$target" dev "$IFACE" table "$TABLE" 2>/dev/null; then
    added=$((added+1))
  else
    log "failed to add route $target via $IFACE (from $owner)"
  fi
done <<<"$desired"

# Убираем только СВОИ устаревшие маршруты — чужие записи таблицы 200 (DNS провайдера,
# локальная сеть, endpoint пира) трогать нельзя, без них коробка теряет связность.
desired_targets=$(printf '%s\n' "$desired" | cut -f2 | sort -u)
removed=0
while IFS=$'\t' read -r owner target; do
  [ -z "${target:-}" ] && continue
  if ! printf '%s\n' "$desired_targets" | grep -qxF "$target"; then
    ip route del "$target" dev "$IFACE" table "$TABLE" 2>/dev/null && removed=$((removed+1))
  fi
done < "$STATE_FILE"

printf '%s\n' "$desired" > "$STATE_FILE"

[ "$removed" -gt 0 ] && log "split routes synced: $added via tunnel, $removed stale removed"
exit 0
