#!/bin/bash
# Production source: /usr/local/sbin/vpn-watchdog.sh
# A fresh AmneziaWG handshake is insufficient: policy rules can disappear while the interface
# stays healthy. Validate the effective route first and repair it idempotently.

IFACE=${VPN_IFACE:-awg0}
ROUTING_TABLE=${VPN_ROUTING_TABLE:-200}
APPLY_SCRIPT=${VPN_APPLY_SCRIPT:-/root/vpn_apply.sh}
PROBE_IP=${VPN_ROUTE_PROBE_IP:-1.1.1.1}

route_policy_ok() {
  ip rule show | grep -Eq "^[[:space:]]*1000:.*fwmark 0x1.*lookup main" || return 1
  ip rule show | grep -Eq "^[[:space:]]*1001:.*lookup ${ROUTING_TABLE}([[:space:]]|$)" || return 1
  ip route show table "$ROUTING_TABLE" | grep -Eq "^default dev ${IFACE}([[:space:]]|$)" || return 1
  ip route get "$PROBE_IP" | grep -Eq "dev ${IFACE}([[:space:]]|$)" || return 1
}

if ! ip link show "$IFACE" >/dev/null 2>&1; then
  logger -t vpn-watchdog "interface $IFACE missing; restarting tunnel"
  systemctl restart "awg-quick@$IFACE"
  exit 0
fi

if ! route_policy_ok; then
  logger -t vpn-watchdog "policy routing missing or ineffective; reapplying"
  if "$APPLY_SCRIPT" && route_policy_ok; then
    logger -t vpn-watchdog "policy routing restored"
    exit 0
  fi
  logger -t vpn-watchdog "policy routing repair failed; restarting tunnel"
  systemctl restart "awg-quick@$IFACE"
  exit 0
fi

now=$(date +%s)
last=$(awg show "$IFACE" latest-handshakes 2>/dev/null | awk '{print $2}' | sort -n | tail -n1)
if [ -n "$last" ] && [ "$last" != 0 ]; then
  age=$((now-last))
else
  age=99999
fi

if [ "$age" -gt 200 ]; then
  if ! curl -s -o /dev/null --max-time 6 https://1.1.1.1; then
    logger -t vpn-watchdog "handshake stale ${age}s and no tunnel internet; restarting"
    systemctl restart "awg-quick@$IFACE"
  fi
fi

