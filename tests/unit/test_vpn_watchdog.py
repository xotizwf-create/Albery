from pathlib import Path


WATCHDOG = Path(__file__).resolve().parents[2] / "deploy" / "vpn-watchdog.sh"


def test_watchdog_checks_effective_policy_route_before_handshake():
    source = WATCHDOG.read_text(encoding="utf-8")

    assert "1000:.*fwmark 0x1.*lookup main" in source
    assert '1001:.*lookup ${ROUTING_TABLE}' in source
    assert 'ip route show table "$ROUTING_TABLE"' in source
    assert 'ip route get "$PROBE_IP"' in source
    assert source.index("if ! route_policy_ok") < source.index("latest-handshakes")


def test_watchdog_reapplies_routes_and_verifies_repair_before_restart():
    source = WATCHDOG.read_text(encoding="utf-8")

    assert 'if "$APPLY_SCRIPT" && route_policy_ok; then' in source
    assert 'systemctl restart "awg-quick@$IFACE"' in source
    assert "policy routing restored" in source

