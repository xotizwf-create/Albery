"""Замер прямой доступности обязан отличать «дошли» от «обслужат».

16.08.2026 замер на проде показал у api.openai.com честный HTTP 403 с телом
unsupported_country_region_territory, а у api.groq.com — 403 Forbidden. По прежнему
правилу «любой HTTP-код означает, что туннель не нужен» замер отнёс мозг агента и
голосовые к работающим напрямую — то есть посоветовал вынести их из туннеля и тем
самым выключить. Здесь это закреплено тестом: отказ по географии остаётся в туннеле.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

PROBE = Path("scripts/vpn_direct_probe.sh")

needs_shell = pytest.mark.skipif(os.name == "nt" and not os.environ.get("MSYSTEM"),
                                 reason="requires a POSIX shell")

# Что отвечает каждый URL: (тело напрямую, код напрямую, код по текущему маршруту).
# Значения взяты из реального замера на проде 16.08.2026.
RESPONSES = {
    "api.openai.com": (
        '{"error":{"code":"unsupported_country_region_territory",'
        '"message":"Country, region, or territory not supported"}}', "403", "401"),
    "chatgpt.com": ('{"error":{"code":"unsupported_country_region_territory"}}', "403", "200"),
    # Тело без узнаваемого признака: ловится вторым сигналом — 403 напрямую при
    # рабочем ответе через туннель.
    "auth.openai.com": ('{"error":"forbidden request"}', "403", "401"),
    "api.groq.com": ('{"error":{"message":"Forbidden"}}', "403", "401"),
    "api.telegram.org": ("", "000", "302"),
    "b24-0xrp3s.bitrix24.ru": ("<html>redirect</html>", "302", "302"),
    "www.googleapis.com": ('{"kind":"discovery#directoryList"}', "200", "200"),
    "oauth2.googleapis.com": ('{"error":"not_found"}', "404", "404"),
    "zoom.us": ('{"reason":"method not allowed"}', "405", "405"),
    "github.com": ("<html>github</html>", "200", "200"),
    "suppliers-api.wildberries.ru": ("", "000", "000"),
    "1.1.1.1": ("<html>cf</html>", "301", "301"),
}


def _fake_curl(tmp_path: Path) -> Path:
    """curl, который отвечает по таблице выше и различает --interface eth0."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)

    branches = []
    for host, (body, direct_code, current_code) in RESPONSES.items():
        branches.append(
            f'  *{host}*)\n'
            f'    body={body!r}; direct_code="{direct_code}"; current_code="{current_code}";;'
        )
    case_block = "\n".join(branches)

    script = f"""#!/bin/bash
direct=0
url=""
prev=""
for a in "$@"; do
  if [ "$prev" = "--interface" ] && [ "$a" = "eth0" ]; then direct=1; fi
  case "$a" in https://*) url="$a";; esac
  prev="$a"
done

body=""; direct_code="200"; current_code="200"
case "$url" in
{case_block}
esac

if [ "$direct" = "1" ]; then code="$direct_code"; else code="$current_code"; fi
# 000 означает, что соединение не состоялось: curl молчит и выходит с ошибкой.
if [ "$code" = "000" ]; then exit 7; fi
printf '%s' "$body"
printf '\\n%s %s' "$code" "0.100000"
"""
    path = bin_dir / "curl"
    path.write_text(script, encoding="utf-8")
    path.chmod(0o755)
    return bin_dir


def _run(tmp_path: Path) -> str:
    bin_dir = _fake_curl(tmp_path)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    # Вывод замера на русском: на Windows locale-кодировка его не прочитает.
    done = subprocess.run(["bash", str(PROBE)], text=True, capture_output=True, env=env,
                          encoding="utf-8", errors="replace")
    assert done.returncode == 0, done.stderr
    return done.stdout


def _line_after(output: str, prefix: str) -> str:
    for line in output.splitlines():
        if line.startswith(prefix):
            return line
    raise AssertionError(f"нет строки {prefix!r} в выводе:\n{output}")


@needs_shell
@pytest.mark.parametrize("service", ["openai-api", "openai-chatgpt", "groq"])
def test_geo_blocked_services_stay_in_tunnel(tmp_path: Path, service: str) -> None:
    """403 с телом про страну — это отказ, а не достижимость."""
    out = _run(tmp_path)
    assert service in _line_after(out, "Оставить в туннеле:")
    assert service not in _line_after(out, "Работает НАПРЯМУЮ")


@needs_shell
def test_geo_blocked_are_named_separately(tmp_path: Path) -> None:
    """Геоблок нужно отличать от «нет связи» — лечится он по-разному."""
    out = _run(tmp_path)
    geo = _line_after(out, "  из них отказ по географии")
    assert "openai-api" in geo and "groq" in geo
    assert "telegram" not in geo  # у Telegram именно нет связи, а не отказ


@needs_shell
def test_unreachable_services_stay_in_tunnel(tmp_path: Path) -> None:
    out = _run(tmp_path)
    assert "telegram" in _line_after(out, "Оставить в туннеле:")


@needs_shell
@pytest.mark.parametrize("service", ["bitrix", "google-api", "google-oauth", "zoom", "github"])
def test_reachable_services_go_direct(tmp_path: Path, service: str) -> None:
    """Ради этого всё и делалось: рабочие сервисы не привязаны к живучести VPN."""
    out = _run(tmp_path)
    assert service in _line_after(out, "Работает НАПРЯМУЮ")


@needs_shell
def test_403_without_geo_body_caught_by_comparison(tmp_path: Path) -> None:
    """Второй сигнал: тело промолчало, но 403 напрямую против рабочего ответа в туннеле."""
    out = _run(tmp_path)
    assert "openai-auth" in _line_after(out, "Оставить в туннеле:")
    assert "openai-auth" in _line_after(out, "  из них отказ по географии")
