"""Мониторинг обязан знать про все службы — иначе их падение молчаливо.

07.08.2026 система разделена на три службы (бот, веб, MCP). Пока selfcheck про новые не знал,
их падение не вызвало бы ни одного алерта: он проверял только albery, albery-tg, hermes-gateway,
nginx и postgresql. Сайт при этом лежал бы полностью, а мониторинг рапортовал «всё чисто».

Второй урок того же дня: проверять «слушает ли порт» бесполезно. Службы тогда поднялись,
порт отвечал, /login отдавал 200 — а пул соединений был сломан, и любая работа с данными
падала через 30 секунд. Поэтому мониторинг обязан ходить в /healthz, который реально берёт
соединение с базой.
"""
from __future__ import annotations

import re
from pathlib import Path

SELFCHECK = Path("scripts/albery_selfcheck.py")


def _source() -> str:
    return SELFCHECK.read_text(encoding="utf-8")


def test_all_split_services_are_watched():
    """Каждая из трёх ролей должна быть в списке критичных служб."""
    source = _source()
    block = re.search(r"CRITICAL_SERVICES\s*=\s*\[(.*?)\]", source, re.S).group(1)
    for service in ("albery", "albery-tg", "albery-web", "albery-mcp"):
        assert f'"{service}"' in block, (
            f"{service} не под наблюдением: её падение не вызовет ни одного алерта"
        )


def test_monitoring_checks_database_reachability_not_just_the_port():
    """Именно эта разница стоила простоя Центра Агента.

    «Порт слушает» и «процесс умеет работать с данными» — разные вещи; со сломанным пулом
    первое выполняется, а второе нет.
    """
    source = _source()
    assert "/healthz" in source, "мониторинг обязан ходить в /healthz, а не только проверять юнит"
    assert 'health.get("database")' in source, "мониторинг обязан смотреть, видит ли роль базу"


def test_database_failure_of_a_role_is_critical():
    """Роль без базы — это лежащая роль, алерт обязан идти мимо антиспам-паузы."""
    source = _source()
    healthz_block = source.split("ROLE_ENDPOINTS.items()", 1)[1].split("# --- PostgreSQL", 1)[0]
    assert healthz_block.count("critical = True") >= 2, (
        "и недоступность /healthz, и потеря базы обязаны быть критичными"
    )


def test_wrong_role_is_reported():
    """Неверная роль = второй комплект фоновых расписаний = двойные уведомления людям."""
    source = _source()
    assert 'health.get("role")' in source, "мониторинг обязан сверять объявленную роль"


def test_monitor_can_be_rehearsed_without_alerting_people():
    """Монитор, который нельзя прогнать не потревожив команду, никто не прогоняет.

    А молчащий монитор ничем не отличается от сломанного, пока не случится авария. Режим
    --dry-run позволяет проверить «поймает ли он поломку» ДО того, как это понадобится.
    """
    source = _source()
    assert 'DRY_RUN = "--dry-run" in sys.argv' in source
    assert "if DRY_RUN:" in source, "notify обязан молчать в режиме проверки"


def test_rehearsal_does_not_swallow_the_next_real_alert():
    """В dry-run нельзя трогать антиспам-паузу: иначе проверка заглушит настоящую тревогу."""
    source = _source()
    assert "if not DRY_RUN:" in source, (
        "состояние антиспама в режиме проверки обновлять нельзя — следующий НАСТОЯЩИЙ "
        "алерт был бы подавлен как повторный"
    )


def test_role_ports_match_the_unit_files():
    """Порты в мониторинге и в unit-файлах разъезжаются молча — сверяем механически."""
    source = _source()
    ports = dict(re.findall(r'"(web|mcp)":\s*(\d+)', source))
    for role, unit_name in (("web", "deploy/albery-web.service"), ("mcp", "deploy/albery-mcp.service")):
        unit = Path(unit_name).read_text(encoding="utf-8")
        bind_port = re.search(r"--bind 127\.0\.0\.1:(\d+)", unit).group(1)
        assert ports.get(role) == bind_port, (
            f"роль {role}: мониторинг смотрит порт {ports.get(role)}, служба слушает {bind_port}"
        )


def test_monitor_detects_hermes_restart_policy_drift():
    source = _source()

    assert 'restart_values.get("RestartUSec") != "30s"' in source
    assert 'restart_values.get("StartLimitIntervalUSec") != "5min"' in source
    assert 'restart_values.get("StartLimitBurst") != "5"' in source
    assert '"RestartMaxDelaySec=" in unit_text' in source
    assert '"RestartSteps=" in unit_text' in source
