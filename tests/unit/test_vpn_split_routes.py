"""Раздельный туннель: в VPN уходит только allowlist, всё остальное — напрямую.

До 16.08.2026 модель была обратной, и часовой простой туннеля унёс с собой Битрикс, Google
и Zoom, которым VPN не нужен вовсе. Теперь через туннель ходит только то, что напрямую
действительно недоступно, — а значит, сбой VPN больше не выключает продукт целиком.

Отдельно закреплено поведение при молчащем резолвере: адреса сервисов за CDN меняются,
поэтому список пересобирается по таймеру. Если в этот момент DNS моргнул, старые адреса
НЕЛЬЗЯ выбрасывать — иначе моргнувший DNS отрезает агента от его же мозга.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SPLIT_ROUTES = Path("deploy/vpn-split-routes.sh")
CONFIG = Path("deploy/vpn-split-domains.conf")

pytestmark = pytest.mark.skipif(os.name == "nt", reason="shell behavior is covered by Linux CI")


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _harness(tmp_path: Path, resolver: str, config: str) -> tuple[dict, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir(exist_ok=True)
    routes = tmp_path / "routes.log"

    _write_executable(fake_bin / "logger", "#!/bin/bash\nexit 0\n")
    _write_executable(
        fake_bin / "ip",
        f"""#!/bin/bash
if [[ $1 == link ]]; then exit 0; fi
if [[ $1 == route ]]; then shift; echo "route $*" >> {routes}; exit 0; fi
exit 0
""",
    )
    _write_executable(fake_bin / "getent", resolver)

    conf = tmp_path / "domains.conf"
    conf.write_text(config, encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        "VPN_SPLIT_CONF": str(conf),
        "VPN_SPLIT_STATE": str(tmp_path / "state" / "routes.state"),
    })
    return env, routes


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(SPLIT_ROUTES)], text=True, capture_output=True,
                          timeout=20, env=env)


_RESOLVER = """#!/bin/bash
case "$2" in
  api.openai.com) printf '104.18.0.1 STREAM api.openai.com\n104.18.0.2 DGRAM\n' ;;
esac
exit 0
"""

_SILENT_RESOLVER = "#!/bin/bash\nexit 0\n"


def test_allowlist_goes_through_the_tunnel(tmp_path: Path):
    env, routes = _harness(tmp_path, _RESOLVER, "host api.openai.com\nnet 149.154.160.0/20\n")

    assert _run(env).returncode == 0
    log = routes.read_text(encoding="utf-8")
    assert "route replace 104.18.0.1/32 dev awg0 table 200" in log
    assert "route replace 104.18.0.2/32 dev awg0 table 200" in log
    assert "route replace 149.154.160.0/20 dev awg0 table 200" in log


def test_rotated_cdn_address_replaces_the_old_one(tmp_path: Path):
    env, routes = _harness(tmp_path, _RESOLVER, "host api.openai.com\n")
    assert _run(env).returncode == 0

    routes.write_text("", encoding="utf-8")
    _write_executable(
        Path(env["PATH"].split(os.pathsep)[0]) / "getent",
        """#!/bin/bash
case "$2" in
  api.openai.com) printf '104.18.0.7 STREAM api.openai.com\n' ;;
esac
exit 0
""",
    )
    assert _run(env).returncode == 0

    log = routes.read_text(encoding="utf-8")
    assert "route replace 104.18.0.7/32 dev awg0 table 200" in log
    assert "route del 104.18.0.1/32 dev awg0 table 200" in log, "устаревший адрес обязан уйти"


def test_silent_resolver_keeps_the_last_known_addresses(tmp_path: Path):
    """DNS моргнул — маршруты к мозгу агента остаются на месте."""
    env, routes = _harness(tmp_path, _RESOLVER, "host api.openai.com\n")
    assert _run(env).returncode == 0

    routes.write_text("", encoding="utf-8")
    _write_executable(Path(env["PATH"].split(os.pathsep)[0]) / "getent", _SILENT_RESOLVER)
    assert _run(env).returncode == 0

    log = routes.read_text(encoding="utf-8")
    assert "route del" not in log, (
        "молчащий резолвер не повод удалять рабочие маршруты — так DNS-моргание "
        "отрезало бы агента от OpenAI"
    )
    assert "route replace 104.18.0.1/32 dev awg0 table 200" in log


def test_missing_tunnel_leaves_routes_untouched(tmp_path: Path):
    """Туннеля нет — это не ошибка: всё, кроме allowlist, работает напрямую."""
    env, routes = _harness(tmp_path, _RESOLVER, "host api.openai.com\n")
    _write_executable(
        Path(env["PATH"].split(os.pathsep)[0]) / "ip",
        f"""#!/bin/bash
if [[ $1 == link ]]; then exit 1; fi
if [[ $1 == route ]]; then shift; echo "route $*" >> {routes}; exit 0; fi
exit 0
""",
    )

    assert _run(env).returncode == 0
    assert not routes.exists() or routes.read_text(encoding="utf-8").strip() == ""


def test_shipped_config_routes_openai_and_nothing_broad(tmp_path: Path):
    """В конфиге не должно быть широких CDN-диапазонов: вместе с сервисом в туннель
    уехала бы половина интернета."""
    text = CONFIG.read_text(encoding="utf-8")
    active = [l.strip() for l in text.splitlines()
              if l.strip() and not l.strip().startswith("#")]
    assert any(l == "host api.openai.com" for l in active), "мозг агента обязан быть в туннеле"
    for line in active:
        if line.startswith("net "):
            prefix = int(line.split("/")[-1])
            assert prefix >= 16, f"слишком широкая подсеть в allowlist: {line}"
