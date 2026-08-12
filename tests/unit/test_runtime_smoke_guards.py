from pathlib import Path


SMOKE = Path(__file__).resolve().parents[2] / "scripts" / "deploy_smoke.py"


def test_deploy_smoke_checks_effective_vpn_health():
    source = SMOKE.read_text(encoding="utf-8")

    assert "/usr/local/sbin/vpn-healthcheck.sh" in source
    assert "effective outbound route or provider reachability is unhealthy" in source


def test_deploy_smoke_requires_connected_or_explicitly_retired_telegram_without_exposing_error():
    source = SMOKE.read_text(encoding="utf-8")

    assert "/root/.hermes/gateway_state.json" in source
    assert 'telegram_state != "connected" and not telegram_retired' in source
    assert 'HERMES_TELEGRAM_RETIRED' in source
    assert 'gateway_state.get("platforms")' in source
    assert 'gateway_state.get("error_message")' not in source
    assert 'albery_global_limited_zoom.sh' in source
