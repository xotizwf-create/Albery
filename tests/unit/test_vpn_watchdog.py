"""Сторож VPN обязан ловить туннель, который «как будто жив».

16.08.2026, живой простой: `awg0` час держал свежие рукопожатия и терял ~90% данных
(0 байт принято против 18 КБ отправленных) — рукопожатия мелкие и с повторами, они
пролезали, полезный трафик нет. Проверка интернета стояла ЗА условием «рукопожатие старше
200 с», поэтому сторож считал такой туннель здоровым и молчал, пока бот был отрезан от
Битрикса, Google и Zoom.

Второй урок того же часа: перезапуск лечит не всё. Когда причина снаружи, сторож дёргал
туннель каждые три минуты около двадцати раз подряд — без толку и с обрывом живых запросов
на каждом круге. После нескольких неудачных попыток он обязан остановиться и позвать человека.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

WATCHDOG = Path("deploy/vpn-watchdog.sh")

# Прогон скрипта требует POSIX-шелла; текстовые проверки структуры работают везде.
needs_shell = pytest.mark.skipif(os.name == "nt", reason="shell behavior is covered by Linux CI")


def test_watchdog_repairs_policy_before_restarting_the_tunnel():
    """Порядок «сначала починить политику, потом дёргать туннель» — из разбора простоя
    Центра Агента: правила пропадают чаще, чем ломается сам туннель, и перезапуск их не
    возвращает."""
    source = WATCHDOG.read_text(encoding="utf-8")

    assert "1000:.*fwmark 0x1.*lookup main" in source
    assert '1001:.*lookup ${ROUTING_TABLE}' in source
    assert 'ip route show table "$ROUTING_TABLE"' in source
    assert 'if "$APPLY_SCRIPT" && route_policy_ok; then' in source
    assert 'systemctl restart "awg-quick@$IFACE"' in source
    assert "policy routing restored" in source
    assert source.index("if ! route_policy_ok") < source.index("if tunnel_carries_data; then")


def test_watchdog_guards_the_rules_that_keep_the_box_reachable():
    """Правила 900/901/902 возвращают ответы сервера в основную таблицу. Без них ответ на
    входящее соединение уходит в туннель, и коробка пропадает из сети целиком."""
    source = WATCHDOG.read_text(encoding="utf-8")

    for prio, port in (("900", "22"), ("901", "443"), ("902", "80")):
        assert f'{prio}:.*sport {port}.*lookup main' in source, (
            f"правило {prio} (порт {port}) обязано проверяться сторожем"
        )


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


def _harness(tmp_path: Path, *, tunnel_carries_data: bool) -> tuple[dict, Path, Path]:
    """Фейковое окружение: политика маршрутизации в порядке, интерфейс на месте,
    рукопожатие свежее. Отличается только одно — доходят ли данные через туннель."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    actions = tmp_path / "actions.log"
    alerts = tmp_path / "alerts.log"

    _write_executable(
        fake_bin / "systemctl",
        f"#!/bin/bash\necho \"systemctl $*\" >> {actions}\n",
    )
    _write_executable(
        fake_bin / "ip",
        """#!/bin/bash
if [[ $1 == link ]]; then exit 0; fi
if [[ $1 == rule ]]; then
  printf '900: from all ipproto tcp sport 22 lookup main\n901: from all ipproto tcp sport 443 lookup main\n902: from all ipproto tcp sport 80 lookup main\n1000: from all fwmark 0x1 lookup main\n1001: from all lookup 200\n'
  exit 0
fi
if [[ $1 == route && $2 == show ]]; then
  printf 'default via 186.246.7.1 dev eth0\n104.18.0.1 dev awg0\n'
  exit 0
fi
exit 0
""",
    )
    # Рукопожатие СВЕЖЕЕ — ровно то состояние, в котором старый сторож молчал.
    _write_executable(
        fake_bin / "awg",
        "#!/bin/bash\necho $'peer\\t'$(date +%s)\n",
    )
    curl_rc = 0 if tunnel_carries_data else 28
    _write_executable(
        fake_bin / "curl",
        f"#!/bin/bash\nexit {curl_rc}\n",
    )
    _write_executable(fake_bin / "logger", "#!/bin/bash\nexit 0\n")

    apply_script = tmp_path / "vpn-apply.sh"
    _write_executable(apply_script, f"#!/bin/bash\necho 'apply' >> {actions}\nexit 0\n")
    split_script = tmp_path / "vpn-split-routes.sh"
    _write_executable(split_script, f"#!/bin/bash\necho 'split' >> {actions}\nexit 0\n")
    notify = tmp_path / "notify.sh"
    _write_executable(notify, f"#!/bin/bash\necho \"$2\" >> {alerts}\nexit 0\n")
    notify_module = tmp_path / "b24_chat_notify.py"
    notify_module.write_text("# stub\n", encoding="utf-8")

    env = os.environ.copy()
    env.update({
        "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
        "VPN_WATCHDOG_STATE_DIR": str(tmp_path / "state"),
        "VPN_APPLY_SCRIPT": str(apply_script),
        "VPN_SPLIT_ROUTES": str(split_script),
        "VPN_WATCHDOG_NOTIFY": str(notify),
        "VPN_WATCHDOG_NOTIFY_MODULE": str(notify_module),
    })
    return env, actions, alerts


def _run(env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(WATCHDOG)], text=True, capture_output=True,
                          timeout=20, env=env)


@needs_shell
def test_fresh_handshake_without_data_is_not_health(tmp_path: Path):
    """Свежее рукопожатие при мёртвых данных обязано вызывать ремонт, а не тишину."""
    env, actions, _ = _harness(tmp_path, tunnel_carries_data=False)

    result = _run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    log = actions.read_text(encoding="utf-8")
    assert "systemctl restart awg-quick@awg0" in log, (
        "туннель без данных обязан быть перезапущен — именно этой проверки не хватило 16.08"
    )


@needs_shell
def test_working_tunnel_is_left_alone_and_refreshes_allowlist(tmp_path: Path):
    """Живой туннель трогать нельзя; заодно обновляется allowlist (адреса за CDN плывут)."""
    env, actions, alerts = _harness(tmp_path, tunnel_carries_data=True)

    result = _run(env)

    assert result.returncode == 0, result.stdout + result.stderr
    log = actions.read_text(encoding="utf-8") if actions.exists() else ""
    assert "systemctl restart" not in log, "рабочий туннель перезапускать нельзя"
    assert "split" in log, "allowlist обязан обновляться, пока туннель жив"
    assert not alerts.exists(), "тревог при здоровом туннеле быть не должно"


@needs_shell
def test_restart_loop_stops_and_calls_a_human(tmp_path: Path):
    """Когда причина снаружи, бесконечный перезапуск — это вред. Нужен человек."""
    env, actions, alerts = _harness(tmp_path, tunnel_carries_data=False)
    env["VPN_WATCHDOG_MAX_RESTARTS"] = "2"

    for _ in range(4):
        assert _run(env).returncode == 0

    restarts = actions.read_text(encoding="utf-8").count("systemctl restart")
    assert restarts == 2, f"перезапусков должно быть ровно 2, а не {restarts}"
    assert alerts.exists(), "после исчерпания попыток обязана уйти тревога владельцу"
    assert "VPN" in alerts.read_text(encoding="utf-8")


@needs_shell
def test_recovery_resets_the_counter(tmp_path: Path):
    """После восстановления счётчик обнуляется — следующий сбой снова получит свои попытки."""
    env, actions, _ = _harness(tmp_path, tunnel_carries_data=False)
    env["VPN_WATCHDOG_MAX_RESTARTS"] = "1"
    _run(env)
    _run(env)  # попытки исчерпаны, перезапусков больше нет

    state = Path(env["VPN_WATCHDOG_STATE_DIR"]) / "vpn-watchdog.fails"
    assert int(state.read_text(encoding="utf-8").strip()) >= 2

    healthy_env, _, _ = _harness(tmp_path / "healthy", tunnel_carries_data=True)
    healthy_env["VPN_WATCHDOG_STATE_DIR"] = env["VPN_WATCHDOG_STATE_DIR"]
    _run(healthy_env)

    assert state.read_text(encoding="utf-8").strip() == "0"


@needs_shell
def test_broken_policy_routing_is_repaired_before_anything_else(tmp_path: Path):
    """Без правил 900/901/902 ответы сервера уходят в туннель и коробка пропадает из сети."""
    env, actions, _ = _harness(tmp_path, tunnel_carries_data=True)
    fake_ip = Path(env["PATH"].split(os.pathsep)[0]) / "ip"
    _write_executable(
        fake_ip,
        """#!/bin/bash
if [[ $1 == link ]]; then exit 0; fi
if [[ $1 == rule ]]; then
  printf '1000: from all fwmark 0x1 lookup main\n1001: from all lookup 200\n'
  exit 0
fi
if [[ $1 == route && $2 == show ]]; then
  printf 'default via 186.246.7.1 dev eth0\n104.18.0.1 dev awg0\n'
  exit 0
fi
exit 0
""",
    )

    assert _run(env).returncode == 0
    assert "apply" in actions.read_text(encoding="utf-8"), (
        "пропавшие правила обязаны чиниться скриптом политики"
    )
