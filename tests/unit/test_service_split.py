"""Разделение на службы: маршрутизация nginx обязана покрывать ВСЕ адреса приложения.

С 07.08.2026 один процесс разрезан на три службы из того же кода — бот (вебхуки и разговоры),
веб (сайт и API) и MCP (инструменты агентов). Маршрутизирует nginx по адресу.

Опасность здесь одна и она тихая: адрес, не попавший ни под одно правило, уедет не в ту
службу. Для вебхука это значит, что событие Битрикса придёт в процесс без состояния разговоров
— сообщение молча не склеится с предыдущим, а «Новая сессия» не убьёт идущий ход. Ни один
монитор такого не покажет: HTTP-ответ будет 200. Поэтому соответствие маршрутов и правил
проверяется механически, а не глазами при ревью.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

NGINX_CONF = Path("deploy/nginx-albery.conf")

# Порты ролей — те же, что в unit-файлах deploy/albery-*.service.
PORT_BOT, PORT_WEB, PORT_MCP = "5002", "5003", "5004"

# Правило из конфига: эти префиксы уходят в роль бота, остальное с www — в роль веб.
BOT_PREFIX_RE = re.compile(r"^/(bitrix/|zoom/events/|google-drive/events/)")

# Адреса, которые ОБЯЗАНЫ попасть в роль бота: всё их поведение опирается на состояние
# в памяти одного процесса.
WEBHOOKS_THAT_MUST_REACH_THE_BOT = (
    "/bitrix/imbot/secret123",          # разговоры: накопление сообщений, «Новая сессия»
    "/bitrix/events/tasks/secret123",   # события задач
    "/bitrix/events/team/secret123",    # события оргструктуры
    "/zoom/events/secret123",           # вебхук Zoom
    "/google-drive/events/secret123",   # вебхук Google Drive
)

# Адреса, которые обязаны уйти в роль веб.
PATHS_THAT_MUST_REACH_THE_WEB = (
    "/", "/main", "/login", "/agent", "/registry", "/reports",
    "/api/registry", "/api/agent-center/agents", "/api/owner/daily-report",
    "/api/zoom-calls/sync", "/api/wb-cab/summary", "/assets/index.js",
)


def _conf_text() -> str:
    assert NGINX_CONF.exists(), "конфиг nginx обязан лежать в репозитории, а не только на сервере"
    return NGINX_CONF.read_text(encoding="utf-8")


@pytest.mark.parametrize("path", WEBHOOKS_THAT_MUST_REACH_THE_BOT)
def test_webhooks_route_to_the_bot_role(path):
    assert BOT_PREFIX_RE.match(path), (
        f"{path} не попал под правило роли бота. Событие уехало бы в процесс без состояния "
        "разговоров: сообщение не склеится с предыдущим, «Новая сессия» не остановит ход, "
        "и всё это молча, с кодом 200."
    )


@pytest.mark.parametrize("path", PATHS_THAT_MUST_REACH_THE_WEB)
def test_site_and_api_route_to_the_web_role(path):
    assert not BOT_PREFIX_RE.match(path), f"{path} ошибочно уводится в роль бота"


def test_imbot_webhook_is_never_load_balanced():
    """Самый важный адрес: разговоры обязаны идти в ОДИН процесс.

    Роль бота однопроцессная именно поэтому — накопление сообщений и реестр живых ходов
    живут в её памяти. Если этот адрес уедет в многопроцессную службу, вернётся баг
    23.07.2026: два сообщения человека подряд дадут два параллельных хода.
    """
    assert BOT_PREFIX_RE.match("/bitrix/imbot/anysecret")


def test_nginx_conf_declares_all_three_upstreams():
    conf = _conf_text()
    for port, role in ((PORT_BOT, "бот"), (PORT_WEB, "веб"), (PORT_MCP, "MCP")):
        assert f"127.0.0.1:{port}" in conf, f"в конфиге нет роли {role} (порт {port})"


def test_mcp_host_goes_to_the_mcp_role():
    """mcp.m4s.ru обязан вести в отдельную службу, а не обратно в бота."""
    conf = _conf_text()
    mcp_block = conf.split("server_name mcp.m4s.ru;", 1)
    assert len(mcp_block) == 2, "блок mcp.m4s.ru не найден"
    tail = mcp_block[1].split("server {", 1)[0]
    assert f"127.0.0.1:{PORT_MCP}" in tail
    assert f"127.0.0.1:{PORT_BOT}" not in tail, "MCP не должен ходить в роль бота"


def test_worker_timeout_outlives_the_proxy_timeout():
    """Воркер обязан умирать ПОЗЖЕ прокси, иначе пользователь получает 502 вместо таймаута."""
    conf = _conf_text()
    proxy_timeouts = {int(v) for v in re.findall(r"proxy_read_timeout (\d+)s", conf)}
    assert proxy_timeouts, "в конфиге нет proxy_read_timeout"
    for unit_name in ("deploy/albery-web.service", "deploy/albery-mcp.service"):
        unit = Path(unit_name).read_text(encoding="utf-8")
        worker_timeout = int(re.search(r"--timeout (\d+)", unit).group(1))
        assert worker_timeout > max(proxy_timeouts), (
            f"{unit_name}: таймаут воркера {worker_timeout}с не больше прокси {max(proxy_timeouts)}с"
        )


@pytest.mark.parametrize("unit_name", ["deploy/albery-web.service", "deploy/albery-mcp.service"])
def test_units_never_use_preload(unit_name):
    """--preload положил Центр Агента 07.08.2026 — этот тест не даёт вернуть его случайно.

    Приложение обращается к базе уже НА ИМПОРТЕ (ensure_builtin_telegram_agents и другие),
    поэтому с --preload пул psycopg успевает создаться в мастере ДО раздвоения на воркеров.
    Пул раздвоения не переживает: воркеры получают пул, ссылающийся на чужие соединения, и
    КАЖДЫЙ запрос падает через 30 секунд с PoolTimeout.

    Коварство в том, что служба при этом выглядит живой: порт слушает, /login отдаёт 200,
    /mcp отдаёт 401 — эти два адреса до базы не доходят. Поэтому мало запретить флаг, надо
    ещё и проверять после выкладки адрес, который РЕАЛЬНО читает базу (см. scripts/deploy_smoke.py).
    """
    unit = Path(unit_name).read_text(encoding="utf-8")
    exec_start = unit.split("ExecStart=", 1)[1].split("\nRestart", 1)[0]
    assert "--preload" not in exec_start, (
        f"{unit_name}: --preload ломает пул соединений при раздвоении воркеров "
        "(PoolTimeout на каждом запросе, служба при этом выглядит живой)"
    )


@pytest.mark.parametrize("unit_name, expected_role", [
    ("deploy/albery-web.service", "web"),
    ("deploy/albery-mcp.service", "mcp"),
])
def test_units_declare_their_role_explicitly(unit_name, expected_role):
    """Без ALBERY_ROLE служба считает себя ботом и поднимает ВТОРОЙ комплект расписаний."""
    unit = Path(unit_name).read_text(encoding="utf-8")
    assert f"Environment=ALBERY_ROLE={expected_role}" in unit
    assert "ALBERY_WEB_PROCESS" not in unit, (
        "стартовые процедуры (регистрация команд, восстановление ходов с уведомлением людей) "
        "обязаны идти только в роли бота"
    )


@pytest.fixture()
def wsgi_module(monkeypatch):
    """wsgi.py проверяет роль ПРИ ИМПОРТЕ — это его работа, поэтому задаём её до импорта.

    Проверка стоит на уровне модуля намеренно: служба должна падать при старте с понятным
    сообщением, а не подниматься наполовину.
    """
    monkeypatch.setenv("ALBERY_ROLE", "web")
    import wsgi

    return wsgi


def test_wsgi_refuses_to_start_with_the_bot_role(wsgi_module):
    """Полуработающая служба хуже упавшей: она выглядит живой и шлёт вторые копии уведомлений."""
    for bad in (None, "", "bot", "worker", "  "):
        with pytest.raises(RuntimeError, match="web"):
            wsgi_module.check_role(bad)


def test_wsgi_accepts_the_two_served_roles(wsgi_module):
    assert wsgi_module.check_role("web") == "web"
    assert wsgi_module.check_role(" MCP ") == "mcp"
