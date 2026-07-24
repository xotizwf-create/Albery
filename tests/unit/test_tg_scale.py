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


def test_messages_of_one_person_are_batched_not_interleaved(tg, monkeypatch):
    """Два сообщения подряд — ОДИН ход с обоими текстами (23.07.2026: раньше каждое уходило
    в свой ход, и вопросы агента накладывались друг на друга). Ходы не перекрываются."""
    order = []

    def turn(batch):
        order.append(("start", [m["text"] for m in batch]))
        time.sleep(0.1)
        order.append(("end", [m["text"] for m in batch]))

    monkeypatch.setattr(tg, "_autoreply_turn", turn)
    monkeypatch.setattr(tg, "_REPLY_DEBOUNCE_S", 0.05)
    msgs = [{"from": {"id": 555}, "text": "первое"}, {"from": {"id": 555}, "text": "второе"}]
    threads = []
    for m in msgs:
        threads.append(threading.Thread(target=tg.maybe_autoreply, args=(m,)))
        threads[-1].start()
        time.sleep(0.01)
    for t in threads:
        t.join(timeout=5)

    assert order[0][0] == "start" and order[1][0] == "end", "ходы не перекрываются"
    assert order[0][1] == ["первое", "второе"], "пачка обязана уйти в один ход целиком"
    assert len(order) == 2, "второго хода для уже забранных сообщений быть не должно"


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


def test_watch_rejects_a_non_telegram_id(tg):
    """Прод 23.07.2026, ожидание задачи 2018: модель передала telegram_id=18 (Bitrix-id
    сотрудника) — доставка билась в PEER_ID_INVALID каждые 20 секунд. Такое ожидание нельзя
    даже регистрировать."""
    import pytest as _pytest

    with _pytest.raises(ValueError, match="не похож на Telegram id"):
        tg.watch_task_for_client(2018, 18, "Договор отправили")


def test_undeliverable_watch_is_cancelled_not_retried_forever(tg, monkeypatch):
    """Адрес недоставим (PEER_ID_INVALID) — ожидание снимается, а не молотит вечно."""
    watch = {"id": 7, "bitrix_task_id": 2018, "deal_id": 96, "telegram_id": 18,
             "kind": "edo", "client_message": "готово", "next_stage": ""}
    updates = []
    monkeypatch.setattr(tg, "_db", _fake_db([watch], updates))
    monkeypatch.setattr(tg, "_task_status", lambda tid: {"status": "5"})
    monkeypatch.setattr(tg, "send_html",
                        lambda *a: (False, "sendMessage: {'error_code': 400, 'description': "
                                           "'Bad Request: PEER_ID_INVALID'}"))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)

    res = tg.check_finished_tasks()

    assert res["failed"] == [], "недоставимый адрес — не «ошибка на повтор», а снятие"
    assert any("cancelled_at" in sql for sql, _ in updates), "ожидание должно сняться"


def test_task_status_is_plain_http_without_risky_imports(tg, monkeypatch):
    """Прод 23.07.2026: `from bitrix import BitrixClient` в процессе tg-агента упал циклическим
    импортом, и сторож не смог узнать статус НИ ОДНОЙ задачи. Статус обязан браться голым HTTP —
    как mcp_call, и с фолбэком на B24_TESTBOT_WEBHOOK_BASE (BITRIX_WEBHOOK_BASE на боксе пуст)."""
    import sys

    monkeypatch.setitem(sys.modules, "bitrix", None)   # любой импорт bitrix здесь — ошибка
    monkeypatch.delenv("BITRIX_WEBHOOK_BASE", raising=False)
    monkeypatch.setenv("B24_TESTBOT_WEBHOOK_BASE", "https://portal/rest/1/key")

    class R:
        status_code = 200
        text = '{"result": {"task": {"id": 2006, "status": "5"}}}'

        def json(self):
            import json as j
            return j.loads(self.text)

    seen = {}
    monkeypatch.setattr(tg.requests, "get",
                        lambda url, params=None, timeout=0: seen.update(url=url) or R())

    st = tg._task_status(2006)

    assert st == {"status": "5"}
    assert "tasks.task.get" in seen["url"] and seen["url"].startswith("https://portal/")


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
