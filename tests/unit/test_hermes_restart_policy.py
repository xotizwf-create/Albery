from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "install_hermes_gateway_restart_policy.py"
SPEC = importlib.util.spec_from_file_location("hermes_restart_policy", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_sanitize_removes_only_unsupported_directives():
    original = (
        "[Service]\n"
        "Restart=always\n"
        "RestartSec=5\n"
        "RestartMaxDelaySec=300\n"
        "RestartSteps=5\n"
        "RestartForceExitStatus=75\n"
    )

    sanitized, removed = MODULE.sanitize_unit_text(original)

    assert removed == ["RestartMaxDelaySec", "RestartSteps"]
    assert "RestartSec=5" in sanitized
    assert "RestartForceExitStatus=75" in sanitized
    assert "RestartMaxDelaySec" not in sanitized
    assert "RestartSteps" not in sanitized


def test_versioned_policy_is_complete():
    policy = (Path(__file__).resolve().parents[2] / "deploy" / "hermes-gateway-restart-policy.conf").read_text(
        encoding="utf-8"
    )

    MODULE.validate_policy_text(policy)
