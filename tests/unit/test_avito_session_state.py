"""Состояние сессии Авито определяется по ответу сайта, а не по адресу страницы.

Живой случай 21.08.2026. При заведении второго аккаунта Авито отдал стену «Доступ
ограничен: проблема с IP», а проверка входа сказала, что мы вошли: она смотрела только на
отсутствие «/login» в адресе, а адрес остался прежним — /profile/messenger.

Цена этого дефекта не в неудобстве. Ровно этой же проверкой воркер решает, что доложить
про сессию: увидев стену, он рапортовал бы «ok» и продолжал ходить вслепую. Сторож здоровья
верит этому же полю и промолчал бы — то есть канал умер бы тихо, а это худший исход.

Отдельный урок: «страница не отдала форму входа» НЕ означает «мы внутри». Признаком входа
может быть только то, что Авито само называет id вошедшего аккаунта.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "avito_worker.py"

BLOCK_PAGE = """<html><head><title>Доступ ограничен: проблема с IP</title></head>
<body><h1>Доступ ограничен: проблема с IP</h1>
<p>Иногда такое случается, чтобы вернуться на сайт нажмите на кнопку Продолжить
для решения капчи</p></body></html>"""

MESSENGER_PAGE = "<html><body><div data-marker='messenger'>Сообщения</div></body></html>"
LOGIN_PAGE = "<html><body><form><input name='login'><input name='password'></form></body></html>"


@pytest.fixture(scope="module")
def worker():
    spec = importlib.util.spec_from_file_location("avito_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Page:
    def __init__(self, url, html):
        self.url = url
        self._html = html

    def content(self):
        return self._html


def _with_session(worker, monkeypatch, user_id):
    monkeypatch.setattr(worker, "own_user_id", lambda page: user_id)


def test_the_ip_wall_is_not_a_successful_login(worker, monkeypatch):
    """Главный дефект: адрес прежний, форма входа не показана — а внутрь нас не пустили."""
    _with_session(worker, monkeypatch, "")
    page = _Page("https://www.avito.ru/profile/messenger", BLOCK_PAGE)

    assert worker.page_state(page) == "blocked"


def test_the_ip_wall_is_reported_as_blocked_not_ok(worker):
    """Сторож здоровья смотрит на это поле — оно обязано отличать стену от живой сессии."""
    status, note, stop = worker.session_report_for("blocked")

    assert status == "blocked"
    assert "капч" in note.lower() or "ip" in note.lower()
    assert stop is False, "стена временная — воркер должен пробовать снова, а не выходить"


def test_a_live_session_is_confirmed_by_avito_itself(worker, monkeypatch):
    """Признак входа — id аккаунта, который называет сам Авито."""
    _with_session(worker, monkeypatch, "198797068")
    page = _Page("https://www.avito.ru/profile/messenger", MESSENGER_PAGE)

    assert worker.page_state(page) == "ok"
    assert worker.session_report_for("ok") == ("ok", "сессия жива", False)


def test_a_page_without_a_session_is_not_ok(worker, monkeypatch):
    """Мессенджер открылся, но Авито не называет id — внутрь мы не попали."""
    _with_session(worker, monkeypatch, "")
    page = _Page("https://www.avito.ru/profile/messenger", MESSENGER_PAGE)

    assert worker.page_state(page) == "unknown"
    status, _, stop = worker.session_report_for("unknown")
    assert status == "unknown" and stop is False


def test_the_login_form_still_means_login(worker, monkeypatch):
    """Старое поведение сохраняется: просят войти — значит просят войти."""
    _with_session(worker, monkeypatch, "")

    assert worker.page_state(_Page("https://www.avito.ru/#login", LOGIN_PAGE)) == "login"
    status, _, stop = worker.session_report_for("login")
    assert status == "needs_login"
    assert stop is True, "повторный вход руками — единственный выход, обход продолжать бессмысленно"


def test_a_broken_page_does_not_pass_for_a_live_session(worker, monkeypatch):
    """Страница вообще не отвечает — это не повод считать, что мы вошли."""
    class _Dead:
        url = "https://www.avito.ru/profile/messenger"

        def content(self):
            raise RuntimeError("Target page, context or browser has been closed")

    _with_session(worker, monkeypatch, "")

    assert worker.page_state(_Dead()) != "ok"


def test_every_state_has_a_report(worker):
    """Нет состояния без разбора: неизвестный вход не должен молча стать «ok»."""
    for state in ("ok", "blocked", "login", "unknown"):
        status, note, stop = worker.session_report_for(state)
        assert status in worker.SESSION_STATUSES, f"{state} -> {status}"
        assert note, f"{state}: пояснение обязано быть — его читает человек"
        assert isinstance(stop, bool)
