from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts.install_vpn_healthcheck import install, validate


def test_versioned_healthcheck_is_fail_closed_with_bounded_retry():
    source = Path("deploy/vpn-healthcheck.sh").read_bytes()

    validate(source)
    text = source.decode("utf-8")
    assert "PROBE_ATTEMPTS=${VPN_HEALTH_PROBE_ATTEMPTS:-3}" in text
    assert text.count("ip rule show") >= 5
    assert 'if [ "$OPENAI_CODE" = "401" ]' in text
    assert "failed %s attempts" in text


def test_installer_backs_up_and_replaces_atomically(tmp_path: Path):
    target = tmp_path / "sbin" / "vpn-healthcheck.sh"
    target.parent.mkdir()
    target.write_text("legacy\n", encoding="utf-8")
    target.chmod(0o700)
    backup = tmp_path / "backup"

    install(target, backup)

    assert (backup / target.name).read_text(encoding="utf-8") == "legacy\n"
    if os.name != "nt":
        assert (backup.stat().st_mode & 0o777) == 0o700
    assert target.read_bytes() == Path("deploy/vpn-healthcheck.sh").read_bytes()
    if os.name != "nt":
        assert (target.stat().st_mode & 0o777) == 0o755


def test_installer_refuses_reusing_backup_directory(tmp_path: Path):
    target = tmp_path / "vpn-healthcheck.sh"
    backup = tmp_path / "backup"
    backup.mkdir()

    with pytest.raises(FileExistsError):
        install(target, backup)


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(os.name == "nt", reason="shell behavior is covered by Linux CI")
@pytest.mark.parametrize(
    ("openai_mode", "expected_rc", "expected_attempts"),
    (("transient", 0, 2), ("sustained", 1, 3)),
)
def test_healthcheck_tolerates_one_blip_but_fails_sustained_provider_outage(
    tmp_path: Path,
    openai_mode: str,
    expected_rc: int,
    expected_attempts: int,
):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(
        fake_bin / "systemctl",
        "#!/bin/bash\n[[ $1 == is-enabled ]] && echo enabled || echo active\n",
    )
    _write_executable(
        fake_bin / "ip",
        """#!/bin/bash
if [[ $1 == link ]]; then exit 0; fi
if [[ $1 == rule ]]; then
  printf '900: from all ipproto tcp sport 22 lookup main\n901: from all ipproto tcp sport 443 lookup main\n902: from all ipproto tcp sport 80 lookup main\n1000: from all fwmark 0x1 lookup main\n1001: from all lookup 200\n'
elif [[ $1 == route && $2 == show ]]; then
  echo 'default dev awg0 scope link'
else
  echo '1.1.1.1 dev awg0 table 200 src 10.8.2.2'
fi
""",
    )
    _write_executable(
        fake_bin / "awg",
        """#!/bin/bash
if [[ $3 == endpoints ]]; then
  echo $'peer\\t95.85.243.43:12345'
else
  echo $'peer\\t'$(date +%s)
fi
""",
    )
    _write_executable(
        fake_bin / "curl",
        """#!/bin/bash
url=${!#}
if [[ $url == *ifconfig.me* ]]; then echo -n 95.85.243.43; exit 0; fi
if [[ $url == *api.openai.com* ]]; then
  n=0; [[ -f $VPN_TEST_COUNTER ]] && n=$(cat "$VPN_TEST_COUNTER")
  n=$((n+1)); echo -n "$n" > "$VPN_TEST_COUNTER"
  if [[ $VPN_TEST_OPENAI_MODE == transient && $n -ge 2 ]]; then echo -n 401; exit 0; fi
  echo -n 000; exit 28
fi
echo -n 302
""",
    )
    _write_executable(fake_bin / "sleep", "#!/bin/bash\nexit 0\n")
    counter = tmp_path / "counter"
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "VPN_TEST_COUNTER": str(counter),
            "VPN_TEST_OPENAI_MODE": openai_mode,
            "VPN_HEALTH_PROBE_DELAY_SECONDS": "0",
        }
    )

    result = subprocess.run(
        ["bash", "deploy/vpn-healthcheck.sh"],
        text=True,
        capture_output=True,
        timeout=15,
        env=env,
    )

    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert int(counter.read_text(encoding="utf-8")) == expected_attempts
    if expected_rc:
        assert "RESULT: PROBLEM" in result.stdout
    else:
        assert "RESULT: OK" in result.stdout
