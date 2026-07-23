"""Поток клиентов и уведомление о выполненной задаче (владелец, 23.07.2026).

Требование владельца: агент должен разом вести много людей и много сделок. До этого цикл
обновлений обрабатывал апдейты строго по одному, а ход мозга занимает десятки секунд — десятый
написавший ждал бы минуты.
"""
from __future__ import annotations

import threading
import time

import pytest


@pytest.fixture
def tg(monkeypatch):
    import tg_agent

    return tg_agent


def test_turns_of_different_people_run_in_parallel(tg, monkeypatch):
    """Раньше замок был один на всю службу: пока агент думал над одним, стояли все."""
    running, peak = [], []
    lock = threading.Lock()

    def slow_turn(cmd, **kw):
        with lock:
            running.append(1)
            peak.append(len(running))
        time.sleep(0.15)
        with lock:
            running.pop()

        class R:
            returncode = 0
            stdout = "ответ"
            stderr = ""
        return R()

    monkeypatch.setattr(tg.subprocess, "run", slow_turn)
    threads = [threading.Thread(target=tg.hermes_answer, args=("prompt", f"s{i}"))
               for i in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert max(peak) > 1, "ходы разных клиентов обязаны идти одновременно"


def test_parallelism_is_capped(tg):
    """Без предела поток лидов положил бы службу: на боксе 2 ГБ, каждый ход — свой процесс."""
    assert tg._HERMES_PARALLEL >= 1
    assert tg._hermes_slots._initial_value == tg._HERMES_PARALLEL


def test_messages_of_one_person_are_serialised(tg, monkeypatch):
    """Иначе два сообщения подряд уходят в два хода, и второй не видит ответа первого."""
    order = []

    def turn(msg):
        order.append(("start", msg["text"]))
        time.sleep(0.1)
        order.append(("end", msg["text"]))

    monkeypatch.setattr(tg, "_autoreply_turn", turn)
    msgs = [{"from": {"id": 555}, "text": "первое"}, {"from": {"id": 555}, "text": "второе"}]
    threads = [threading.Thread(target=tg.maybe_autoreply, args=(m,)) for m in msgs]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Ходы одного человека не перекрываются: каждый «start» закрыт своим «end».
    assert order[0][0] == "start" and order[1][0] == "end"
    assert order[2][0] == "start" and order[3][0] == "end"


def test_different_people_get_their_own_lock(tg):
    assert tg.dialog_lock(1) is tg.dialog_lock(1)
    assert tg.dialog_lock(1) is not tg.dialog_lock(2)


# --- уведомление клиента о выполненной задаче ------------------------------------------------

def test_client_is_told_when_the_task_is_closed(tg, monkeypatch):
    """23.07.2026: договор ушёл в ЭДО, задачу закрыли — а клиент об этом не узнал."""
    watch = {"id": 1, "bitrix_task_id": 1972, "deal_id": 86, "telegram_id": 555,
             "kind": "edo", "client_message": "Договор отправили вам в ЭДО", "next_stage": ""}
    sent, updates = [], []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "5"})
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)

    res = tg.check_finished_tasks()

    assert res["notified"] == 1
    assert sent == [(555, "Договор отправили вам в ЭДО")]
    assert any("notified_at" in sql for sql, _ in updates), "иначе сообщение уйдёт повторно"


def test_open_task_does_not_notify_anyone(tg, monkeypatch):
    """Пока сотрудник не закрыл задачу, клиенту сообщать нечего."""
    watch = {"id": 1, "bitrix_task_id": 1972, "deal_id": None, "telegram_id": 555,
             "kind": "edo", "client_message": "готово", "next_stage": ""}
    sent = []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], []))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "2"})
    monkeypatch.setattr(tg, "send_html", lambda *a: sent.append(a) or (True, ""))

    res = tg.check_finished_tasks()

    assert res["notified"] == 0 and res["still_open"] == 1 and sent == []


def test_undelivered_message_is_not_marked_as_sent(tg, monkeypatch):
    """Иначе клиент не узнает ничего, а система будет считать, что сказала."""
    watch = {"id": 1, "bitrix_task_id": 1972, "deal_id": None, "telegram_id": 555,
             "kind": "edo", "client_message": "готово", "next_stage": ""}
    updates = []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "5"})
    monkeypatch.setattr(tg, "send_html", lambda *a: (False, "чат недоступен"))

    res = tg.check_finished_tasks()

    assert res["notified"] == 0 and res["failed"]
    assert not any("notified_at" in sql for sql, _ in updates)


def test_watchdog_actually_drives_the_check(tg, monkeypatch):
    """Главная жалоба 23.07.2026: владелец закрыл задачу — клиенту не ушло НИЧЕГО.

    Механизм ожиданий существовал, но check_finished_tasks не вызывался ниоткуда: в докстринге
    значился «сторож», которого никто не написал. Сторож обязан крутиться в службе tg-агента."""
    calls = []
    monkeypatch.setattr(tg, "check_finished_tasks",
                        lambda: calls.append(1) or {"notified": 0, "failed": []})
    monkeypatch.setattr(tg, "_TASK_WATCH_INTERVAL_S", 0.01)

    th = tg.start_task_watchdog()
    time.sleep(0.12)

    assert th.daemon, "сторож не должен держать процесс при остановке службы"
    assert len(calls) >= 2, "сторож должен гонять проверку регулярно, а не один раз"


def test_same_meaning_watch_is_not_sent_twice(tg, monkeypatch):
    """23.07.2026, сделка 92: два ожидания (задачи 1996 и 2006) с одним текстом. Обе задачи
    закрыты — клиент должен получить сообщение ОДИН раз, второе ожидание снимается."""
    watches = [
        {"id": 3, "bitrix_task_id": 1996, "deal_id": 92, "telegram_id": 555,
         "kind": "edo", "client_message": "Договор отправили вам в ЭДО", "next_stage": ""},
        {"id": 4, "bitrix_task_id": 2006, "deal_id": 92, "telegram_id": 555,
         "kind": "edo", "client_message": "Договор отправили вам в ЭДО", "next_stage": ""},
    ]
    sent, updates = [], []
    monkeypatch.setattr(tg, "_db", _fake_db(watches, updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "5"})
    monkeypatch.setattr(tg, "send_html",
                        lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)

    res = tg.check_finished_tasks()

    assert len(sent) == 1, "клиент получил одно и то же дважды — это и есть жалоба на дубли"
    assert res["notified"] == 1
    assert any("cancelled_at" in sql for sql, _ in updates), "второе ожидание должно сняться"


def test_watch_for_deleted_task_is_cancelled(tg, monkeypatch):
    """Задачу удалили из Битрикса (реальная 1994, 23.07.2026): портал отдаёт 200 без задачи.
    Ожидание должно сняться, а не висеть вечно, съедая каждый проход сторожа."""
    watch = {"id": 2, "bitrix_task_id": 1994, "deal_id": 90, "telegram_id": 555,
             "kind": "edo", "client_message": "готово", "next_stage": ""}
    sent, updates = [], []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": ""})
    monkeypatch.setattr(tg, "send_html", lambda *a: sent.append(a) or (True, ""))

    res = tg.check_finished_tasks()

    assert sent == [] and res["notified"] == 0
    assert res["still_open"] == 0, "удалённая задача не «открыта» — её больше нет"
    assert any("cancelled_at" in sql for sql, _ in updates), "мёртвое ожидание должно сняться"


def test_next_stage_moves_through_http_mcp(tg, monkeypatch):
    """Стадию двигаем через mcp_call (HTTP): импортировать context_server в процесс tg-агента
    нельзя — его импорт запускает живые планировщики (см. комментарий у mcp_call)."""
    watch = {"id": 1, "bitrix_task_id": 1972, "deal_id": 86, "telegram_id": 555,
             "kind": "edo", "client_message": "готово", "next_stage": "C16:FINAL_INVOICE"}
    moved, updates = [], []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "5"})
    monkeypatch.setattr(tg, "send_html", lambda *a: (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg, "mcp_call", lambda tool, args: moved.append((tool, args)) or {})

    res = tg.check_finished_tasks()

    assert res["notified"] == 1
    assert moved and moved[0][0] == "update_crm_deal"
    assert moved[0][1]["deal_id"] == 86 and moved[0][1]["stage"] == "C16:FINAL_INVOICE"


def _fake_db(rows, updates):
    """БД-заглушка: выдаёт ожидания и запоминает изменения."""
    import contextlib

    class _Cur:
        def execute(self, sql, params=None):
            flat = " ".join(str(sql).split())
            if flat.startswith("UPDATE"):
                updates.append((flat, list(params or [])))
            self._rows = rows if flat.startswith("SELECT") else []

        def fetchall(self):
            return list(getattr(self, "_rows", []))

        def fetchone(self):
            got = getattr(self, "_rows", [])
            return got[0] if got else None

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    class _Conn:
        def cursor(self):
            return _Cur()

    @contextlib.contextmanager
    def fake():
        yield _Conn()

    return fake
