"""Страница мониторинга не имеет права ждать третью сторону внутри HTTP-запроса,
а тишина в чатах не имеет права выглядеть поломкой.

Живой случай 16.08.2026 (владелец: «сайт открывается очень медленно и постоянно
приходят уведомления о сбоях»). В журнале nginx все 13 обрывов за сутки пришлись
ровно на /api/agent-center/monitoring: браузер отменяет запрос через 30 с
(fetchJsonSafe), поэтому в логе стоит 499. Причина — ping Битрикса, токен Zoom и
портальный справочник имён считались ПРЯМО в запросе, а BitrixClient.call ждёт
ответа 60 с и пробует два пути подряд, то есть до 120 с на один вызов. Пока поток
ждал, он занимал один из восьми потоков единственного веб-воркера, и медленный
Битрикс подвешивал весь Центр Агента, а не только карточку здоровья.

Второй симптом того же дня: в выходные боту никто не писал, «Мозг агента (Hermes)»
уходил в warn просто по возрасту последнего хода, попадал в payload['problems'] и
сторож здоровья слал владельцу «проблемы» каждые три часа. Тишина — не поломка.
"""

import threading
import time
from datetime import timedelta

import pytest

import app  # noqa: F401 — импорт до agent_center: правило циклического импорта
import agent_center
from app import msk_now


def _clear_probe_caches() -> None:
    """Сбрасывает кэши внешних проб, как бы они ни назывались в текущей версии."""
    legacy = getattr(agent_center, "_HEALTH_CACHE", None)
    if isinstance(legacy, dict):
        legacy.update(at=0.0, zoom_at=0.0, bitrix=None, zoom=None)
    probes = getattr(agent_center, "_PROBES", None)
    if isinstance(probes, dict):
        probes.clear()


def _stats_row(*, last_turn_minutes_ago: int | None, last_ok_minutes_ago: int | None) -> dict:
    now = msk_now()
    return {
        "turns_today": 0,
        "turns_yday_same": 0,
        "avg_today": None,
        "avg_yday": None,
        "errors_24h": 0,
        "last_error_at": None,
        "dialogs_7d": 0,
        "last_turn_at": None if last_turn_minutes_ago is None else now - timedelta(minutes=last_turn_minutes_ago),
        "last_ok_at": None if last_ok_minutes_ago is None else now - timedelta(minutes=last_ok_minutes_ago),
    }


def _responder_for(stats: dict):
    def responder(sql: str, params):
        if "turns_today" in sql:
            return stats
        if "bitrix_error_reports" in sql:
            return []
        if "zoom_calls" in sql or "company_drive_sources" in sql:
            return {"m": None}
        if "agent_access" in sql:
            return []
        return []

    return responder


@pytest.fixture()
def monitoring_env(monkeypatch, fake_pg):
    """Общая обвязка: базы нет, git и память не трогаем, Zoom и справочник имён молчат."""
    _clear_probe_caches()
    monkeypatch.setattr(agent_center, "_git_info", lambda: {"at": 0.0, "head": "test", "log": []})
    monkeypatch.setattr(agent_center, "_server_memory", lambda: None)

    import zoom

    monkeypatch.setattr(zoom, "zoom_access_token", lambda *a, **k: "", raising=False)

    import b24bot

    monkeypatch.setattr(b24bot, "_b24_portal_user_directory", lambda *a, **k: {}, raising=False)
    yield fake_pg
    _clear_probe_caches()


def test_slow_bitrix_does_not_hold_the_request(monkeypatch, monitoring_env):
    """Медленный Битрикс не должен задерживать ответ страницы мониторинга.

    Воспроизводит боевой обрыв: вызов Битрикса висит, а payload обязан вернуться
    сразу — иначе браузер обрывает запрос на тридцатой секунде (499 в nginx) и
    вместе с ним стоит весь веб-процесс."""
    monitoring_env(agent_center, responder=_responder_for(
        _stats_row(last_turn_minutes_ago=10, last_ok_minutes_ago=10)))

    import b24bot

    released = threading.Event()
    entered = threading.Event()

    def _hanging_call(*args, **kwargs):
        entered.set()
        released.wait(timeout=30)  # висит как настоящий недоступный портал
        return {"result": "2026-08-16T00:00:00+03:00"}

    monkeypatch.setattr(b24bot, "b24_testbot_client", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(b24bot, "_b24_testbot_call", _hanging_call, raising=False)

    try:
        started = time.perf_counter()
        payload = agent_center.monitoring_payload(1, "all")
        elapsed = time.perf_counter() - started
    finally:
        released.set()

    assert elapsed < 1.0, (
        f"мониторинг ждал внешний вызов {elapsed:.1f} с — при недоступном Битриксе "
        "это те самые 30+ секунд и обрыв запроса браузером")
    assert payload["health"], "карточка здоровья должна собираться и без ответа Битрикса"


def test_bitrix_probe_result_appears_after_background_refresh(monkeypatch, monitoring_env):
    """Проба всё-таки выполняется — просто в фоне, и её результат виден следующему запросу."""
    monitoring_env(agent_center, responder=_responder_for(
        _stats_row(last_turn_minutes_ago=10, last_ok_minutes_ago=10)))

    import b24bot

    monkeypatch.setattr(b24bot, "b24_testbot_client", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(b24bot, "_b24_testbot_call",
                        lambda *a, **k: {"result": "ok"}, raising=False)

    agent_center.monitoring_payload(1, "all")  # запускает фоновое обновление
    deadline = time.time() + 10
    while time.time() < deadline and agent_center._bitrix_ping_ms() is None:
        time.sleep(0.05)

    payload = agent_center.monitoring_payload(1, "all")
    bitrix_card = next(h for h in payload["health"] if h["label"] == "Bitrix REST")
    assert bitrix_card["type"] == "ok"
    assert "ok" in bitrix_card["status"]


def test_silence_is_not_a_problem(monkeypatch, monitoring_env):
    """Выходные: боту сутки никто не писал. Это тишина, а не сбой мозга.

    Раньше карточка уходила в warn по одному лишь возрасту последнего хода, попадала
    в problems, и сторож здоровья будил владельца каждые три часа."""
    monitoring_env(agent_center, responder=_responder_for(
        _stats_row(last_turn_minutes_ago=2000, last_ok_minutes_ago=2000)))

    import b24bot

    monkeypatch.setattr(b24bot, "b24_testbot_client", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(b24bot, "_b24_testbot_call", lambda *a, **k: {"result": "ok"}, raising=False)

    payload = agent_center.monitoring_payload(1, "all")
    brain = next(h for h in payload["health"] if h["label"].startswith("Мозг агента"))
    assert brain["type"] == "ok", "молчание сотрудников не является проблемой системы"
    assert not any(p.startswith("Мозг агента") for p in payload["problems"])


def test_watchdog_cooldown_key_ignores_the_changing_tail():
    """Приглушение повторов обязано держаться за название проверки, а не за её текст.

    Текст несёт возраст, и назавтра «1 дн назад» становится «2 дн назад» — ключ менялся,
    приглушение обнулялось, и владелец получал то же самое уведомление заново."""
    today = "Мозг агента (Hermes): ходы идут, успешных нет — последний 1 дн назад"
    tomorrow = "Мозг агента (Hermes): ходы идут, успешных нет — последний 2 дн назад"
    assert agent_center._watchdog_cooldown_key(today) == agent_center._watchdog_cooldown_key(tomorrow)
    assert agent_center._watchdog_cooldown_key(today) != agent_center._watchdog_cooldown_key("Zoom API: токен не выдаётся")


def test_watchdog_alert_goes_to_bitrix_first(monkeypatch):
    """Сторож слал тревоги ТОЛЬКО в Telegram, токена которого на коробке нет, — и они
    молча терялись («telegram token/chat not configured» в журнале). Основной канал
    владельца — группа «Уведомления» в Битриксе."""
    import b24bot

    sent: list[str] = []

    def _bitrix(text, *args, **kwargs):
        sent.append("bitrix")
        return True, None

    def _telegram(text, *args, **kwargs):
        sent.append("telegram")
        return True, None

    monkeypatch.setattr(b24bot, "_albery_bitrix_notify", _bitrix, raising=False)
    monkeypatch.setattr(b24bot, "_albery_tg_notify", _telegram, raising=False)

    ok, err = agent_center._watchdog_notify("проверка")
    assert (ok, err) == (True, None)
    assert sent == ["bitrix"], "Telegram — только резерв, а не основной канал"


def test_watchdog_falls_back_to_telegram_when_bitrix_is_down(monkeypatch):
    """Битрикс мы тоже мониторим, поэтому тревога о нём не должна уходить только в него."""
    import b24bot

    sent: list[str] = []

    def _bitrix(text, *args, **kwargs):
        sent.append("bitrix")
        return False, "read timeout"

    def _telegram(text, *args, **kwargs):
        sent.append("telegram")
        return True, None

    monkeypatch.setattr(b24bot, "_albery_bitrix_notify", _bitrix, raising=False)
    monkeypatch.setattr(b24bot, "_albery_tg_notify", _telegram, raising=False)

    ok, _ = agent_center._watchdog_notify("проверка")
    assert ok is True
    assert sent == ["bitrix", "telegram"]


def test_turns_without_success_is_still_a_problem(monkeypatch, monitoring_env):
    """А вот когда ходы идут, но ни один не завершился успехом — это настоящая поломка."""
    monitoring_env(agent_center, responder=_responder_for(
        _stats_row(last_turn_minutes_ago=5, last_ok_minutes_ago=4000)))

    import b24bot

    monkeypatch.setattr(b24bot, "b24_testbot_client", lambda *a, **k: object(), raising=False)
    monkeypatch.setattr(b24bot, "_b24_testbot_call", lambda *a, **k: {"result": "ok"}, raising=False)

    payload = agent_center.monitoring_payload(1, "all")
    brain = next(h for h in payload["health"] if h["label"].startswith("Мозг агента"))
    assert brain["type"] == "warn"
    assert any(p.startswith("Мозг агента") for p in payload["problems"])
