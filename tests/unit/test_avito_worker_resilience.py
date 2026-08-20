"""Воркер Авито переживает обрыв связи с Albery, а не умирает от него.

Живой случай 20.08.2026. Воркер отработал час штатно, потом Albery на мгновение стал
недоступен (перезапуск службы, обрыв канала — рядовое событие), и процесс УПАЛ с
http.client.RemoteDisconnected. Канал Авито замолчал молча: переписка перестала приходить,
ответы перестали уходить, и узнать об этом можно было только от сторожа — через два часа
и только в будни 9–19.

Первопричина в urllib, а не в нашем коде на первый взгляд: в CPython
AbstractHTTPHandler.do_open заворачивает в URLError только ошибки ФАЗЫ ЗАПРОСА, а
h.getresponse() стоит ВНЕ этого try. Поэтому обрыв на фазе ответа прилетает как
RemoteDisconnected (ConnectionResetError + BadStatusLine) и пролетает мимо
`except URLError`, на который расчитан клиент.

Отсюда правило: клиент обязан заворачивать ошибки ОБЕИХ фаз, а цикл обхода — не падать
целиком из-за одного неожиданного исключения.
"""
from __future__ import annotations

import http.client
import importlib.util
import socket
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "scripts" / "avito_worker.py"


@pytest.fixture(scope="module")
def worker():
    spec = importlib.util.spec_from_file_location("avito_worker", WORKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def client(worker):
    return worker.Albery("https://example.invalid", "token")


def _raises(worker, monkeypatch, error):
    def boom(*_a, **_k):
        raise error
    monkeypatch.setattr(worker.urlrequest, "urlopen", boom)


def test_a_dropped_response_is_a_recoverable_error(worker, client, monkeypatch):
    """Тот самый случай: связь оборвалась на фазе ответа."""
    _raises(worker, monkeypatch,
            http.client.RemoteDisconnected("Remote end closed connection without response"))

    with pytest.raises(RuntimeError) as caught:
        client.post("/api/avito-worker/inbound", {})

    assert "Albery" in str(caught.value)


def test_a_reset_connection_is_a_recoverable_error(worker, client, monkeypatch):
    """WinError 10054 — «удалённый хост принудительно разорвал подключение»."""
    _raises(worker, monkeypatch, ConnectionResetError(10054, "соединение разорвано"))

    with pytest.raises(RuntimeError):
        client.post("/api/avito-worker/inbound", {})


def test_a_timeout_is_a_recoverable_error(worker, client, monkeypatch):
    """Молчание сервера дольше таймаута — тоже не повод ронять зеркало."""
    _raises(worker, monkeypatch, socket.timeout("timed out"))

    with pytest.raises(RuntimeError):
        client.post("/api/avito-worker/inbound", {})


def test_a_truncated_body_is_a_recoverable_error(worker, client, monkeypatch):
    """Ответ оборвался на середине — HTTPException, не URLError."""
    _raises(worker, monkeypatch, http.client.IncompleteRead(b"", 10))

    with pytest.raises(RuntimeError):
        client.post("/api/avito-worker/inbound", {})


def test_a_broken_body_is_a_recoverable_error(worker, client, monkeypatch):
    """Пришло не-JSON (страница ошибки nginx при перезапуске) — тоже не падение процесса."""
    class _Response:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def read(self): return b"<html>502 Bad Gateway</html>"

    monkeypatch.setattr(worker.urlrequest, "urlopen", lambda *a, **k: _Response())

    with pytest.raises(RuntimeError):
        client.post("/api/avito-worker/inbound", {})


def test_a_programming_error_is_not_disguised_as_a_network_blip(worker, client, monkeypatch):
    """Границу держим честно: наша собственная ошибка не должна выглядеть обрывом связи."""
    _raises(worker, monkeypatch, TypeError("это дефект кода, а не сети"))

    with pytest.raises(TypeError):
        client.post("/api/avito-worker/inbound", {})


class _Boom:
    """Клиент, у которого очередь всегда взрывается неожиданным образом."""

    def __init__(self, error):
        self.error = error

    def claim_outbox(self, *_a, **_k):
        raise self.error


@pytest.mark.parametrize("error", [
    KeyError("outbox_id"),          # ответ сервера сменил форму
    ValueError("плохое число"),     # разбор ответа
    ConnectionResetError(10054, "соединение разорвано"),
])
def test_the_outbox_pass_survives_an_unexpected_error(worker, error):
    """Разбор очереди ловил только RuntimeError — любое другое исключение убивало зеркало.

    Цена дефекта несимметрична: неразобранная очередь стоит одного обхода (двадцать
    секунд), а упавший процесс — молчания канала до тех пор, пока это кто-нибудь заметит.
    """
    worker.drain_outbox(_Boom(error), None, "worker-1", "main")


def test_a_failure_in_the_queue_is_reported_not_swallowed(worker, capsys):
    """Проглотить молча тоже нельзя: в журнале должна остаться причина."""
    worker.drain_outbox(_Boom(KeyError("outbox_id")), None, "worker-1", "main")

    assert "очеред" in capsys.readouterr().out.lower()
