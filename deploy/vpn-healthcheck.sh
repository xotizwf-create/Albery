#!/bin/bash
# Fail closed on sustained VPN/provider failure while tolerating one transient network probe.
# Production target: /usr/local/sbin/vpn-healthcheck.sh

IFACE=${VPN_IFACE:-awg0}
ROUTING_TABLE=${VPN_ROUTING_TABLE:-200}
PROBE_IP=${VPN_ROUTE_PROBE_IP:-1.1.1.1}
PROBE_ATTEMPTS=${VPN_HEALTH_PROBE_ATTEMPTS:-3}
PROBE_DELAY=${VPN_HEALTH_PROBE_DELAY_SECONDS:-2}
OPENAI_HEALTH_URL=${OPENAI_HEALTH_URL:-https://api.openai.com/v1/models}
rc=0

retry_outbound_ip() {
  local attempt value
  OUTBOUND_ATTEMPTS_USED=0
  OUTBOUND_IP=""
  for ((attempt=1; attempt<=PROBE_ATTEMPTS; attempt++)); do
    OUTBOUND_ATTEMPTS_USED=$attempt
    value=$(curl -fsS --interface "$IFACE" --connect-timeout 5 --max-time 12 https://ifconfig.me/ip 2>/dev/null || true)
    if [ -n "$EXPECTED_EXIT" ] && [ "$value" = "$EXPECTED_EXIT" ]; then
      OUTBOUND_IP=$value
      return 0
    fi
    OUTBOUND_IP=$value
    [ "$attempt" -lt "$PROBE_ATTEMPTS" ] && sleep "$PROBE_DELAY"
  done
  return 1
}

retry_openai() {
  local attempt value
  OPENAI_ATTEMPTS_USED=0
  OPENAI_CODE="000"
  for ((attempt=1; attempt<=PROBE_ATTEMPTS; attempt++)); do
    OPENAI_ATTEMPTS_USED=$attempt
    value=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 5 --max-time 15 \
      "$OPENAI_HEALTH_URL" 2>/dev/null || true)
    OPENAI_CODE=${value:-000}
    # An unauthenticated request must reach OpenAI and be rejected by authentication.
    if [ "$OPENAI_CODE" = "401" ]; then
      return 0
    fi
    [ "$attempt" -lt "$PROBE_ATTEMPTS" ] && sleep "$PROBE_DELAY"
  done
  return 1
}

echo "== AmneziaWG VPN healthcheck $(date -u) =="
enabled=$(systemctl is-enabled "awg-quick@$IFACE" 2>/dev/null || true)
active=$(systemctl is-active "awg-quick@$IFACE" 2>/dev/null || true)
printf '%-30s %s / %s\n' "service enabled/active" "$enabled" "$active"
{ [ "$enabled" = "enabled" ] && [ "$active" = "active" ]; } || rc=1

if ip link show "$IFACE" >/dev/null 2>&1; then
  printf '%-30s present\n' "interface $IFACE"
else
  printf '%-30s MISSING\n' "interface $IFACE"
  rc=1
fi

# Раздельный туннель (с 16.08.2026): по умолчанию НАПРЯМУЮ, в туннель — только allowlist.
# Раньше здесь требовался `default dev awg0`, то есть весь трафик через VPN; та модель стоила
# часового простоя, забрав с собой Битрикс, Google и Zoom, которым туннель не нужен.
policy_ok=1
ip rule show | grep -Eq '^[[:space:]]*900:.*sport 22.*lookup main' || policy_ok=0
ip rule show | grep -Eq '^[[:space:]]*901:.*sport 443.*lookup main' || policy_ok=0
ip rule show | grep -Eq '^[[:space:]]*902:.*sport 80.*lookup main' || policy_ok=0
ip rule show | grep -Eq '^[[:space:]]*1000:.*fwmark 0x1.*lookup main' || policy_ok=0
ip rule show | grep -Eq "^[[:space:]]*1001:.*lookup ${ROUTING_TABLE}([[:space:]]|$)" || policy_ok=0
ip route show table "$ROUTING_TABLE" | grep -Eq "^default via .* dev " || policy_ok=0
ip route show table "$ROUTING_TABLE" | grep -Eq "dev ${IFACE}([[:space:]]|$)" || policy_ok=0
printf '%-30s %s\n' "policy route" "$([ "$policy_ok" -eq 1 ] && echo OK || echo PROBLEM)"
[ "$policy_ok" -eq 1 ] || rc=1

# Выходной адрес спрашиваем ЧЕРЕЗ туннель: маршрут по умолчанию теперь прямой, и без явной
# привязки к интерфейсу проба вернула бы собственный адрес коробки и всегда «проходила» бы.
EXPECTED_EXIT=$(awg show "$IFACE" endpoints 2>/dev/null | awk 'NF>=2{print $2; exit}' | sed -E 's/:[0-9]+$//' | tr -d '[]')
if retry_outbound_ip; then
  printf '%-30s %s (expected %s; attempt %s/%s)\n' \
    "outbound IP" "$OUTBOUND_IP" "${EXPECTED_EXIT:-?}" "$OUTBOUND_ATTEMPTS_USED" "$PROBE_ATTEMPTS"
else
  printf '%-30s %s (expected %s; failed %s attempts)\n' \
    "outbound IP" "${OUTBOUND_IP:-empty}" "${EXPECTED_EXIT:-?}" "$PROBE_ATTEMPTS"
  rc=1
fi

if retry_openai; then
  printf '%-30s HTTP %s (attempt %s/%s)\n' \
    "openai reachability" "$OPENAI_CODE" "$OPENAI_ATTEMPTS_USED" "$PROBE_ATTEMPTS"
else
  printf '%-30s HTTP %s (failed %s attempts)\n' \
    "openai reachability" "$OPENAI_CODE" "$PROBE_ATTEMPTS"
  rc=1
fi

# Read the handshake after the external probes: a healthy idle tunnel refreshes on demand.
now=$(date +%s)
last=$(awg show "$IFACE" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -n | tail -n1)
if [ -n "$last" ] && [ "$last" != 0 ]; then
  age=$((now-last))
else
  age=99999
fi
printf '%-30s %s sec ago\n' "last handshake" "$age"
[ "$age" -lt 200 ] || rc=1

local_code=$(curl -sS -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 \
  http://127.0.0.1:5002 2>/dev/null || true)
printf '%-30s HTTP %s\n' "local app :5002" "${local_code:-000}"
{ [ -n "$local_code" ] && [ "$local_code" != "000" ]; } || rc=1

echo "----------------------------------------------"
if [ "$rc" -eq 0 ]; then
  echo "RESULT: OK"
else
  echo "RESULT: PROBLEM (rc=$rc)"
fi
exit "$rc"
