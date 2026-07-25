"""Режим тестирования: эскалации уходят в тестовую группу, а не в рабочую.

Владелец 25.07.2026: «пока что сообщения-эскалации пусть уходят от клиентов в другую группу;
во вкладке воронки — надпись "Идёт тестирование" и галочка».

Зачем это защищено тестами: пока база знаний по ИУ не наполнена, агент часто не находит ответ и
уносит вопрос людям (в выгрузке за 24–25.07.2026 так закончились ВСЕ четыре диалога, дошедшие до
вопросов). Ошибка маршрутизации здесь стоит дорого в обе стороны: шум проверок в рабочей группе
мешает людям отвечать реальным клиентам, а вопрос живого клиента, молча ушедший в тестовую
группу, не получит ответа вообще.
"""
from __future__ import annotations

import contextlib

import funnel_scenario as fs


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    def execute(self, sql, params=None):
        return None

    def fetchall(self):
        return self.rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeConn:
    def __init__(self, rows):
        self.rows = rows

    def cursor(self):
        return FakeCursor(self.rows)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def db_with(rows):
    @contextlib.contextmanager
    def db():
        yield FakeConn(rows)
    return db


def _funnel_row(**over):
    row = {"funnel_id": 16, "stage_id": "", "trigger": "", "need": "", "action": "",
           "enabled": True, "testing": False}
    row.update(over)
    return row


def setup_function(_):
    fs.invalidate()


def test_testing_is_off_until_the_owner_turns_it_on():
    assert fs.testing_mode(db_with([_funnel_row()]), 16) is False


def test_testing_flag_reaches_the_agent():
    """Галочка в кабинете обязана доходить до агента — иначе переключатель бесполезен."""
    assert fs.testing_mode(db_with([_funnel_row(testing=True)]), 16) is True


def test_no_settings_row_means_no_testing():
    assert fs.testing_mode(db_with([]), 16) is False


def test_database_failure_keeps_escalations_in_the_working_group():
    """Сбой базы не имеет права молча увести вопрос живого клиента в тестовую группу."""
    @contextlib.contextmanager
    def broken_db():
        raise RuntimeError("база недоступна")
        yield  # pragma: no cover

    assert fs.testing_mode(broken_db, 16) is False


def test_testing_does_not_disable_the_agent():
    """Тестирование меняет только адресата эскалаций, работу агента оно не останавливает."""
    db = db_with([_funnel_row(testing=True, enabled=True)])

    assert fs.testing_mode(db, 16) is True
    assert fs.agent_enabled(db, 16) is True


def test_old_settings_row_without_the_column_is_read_as_not_testing():
    """Колонка добавлена миграцией 067: строка без неё не должна ронять чтение настроек."""
    row = _funnel_row()
    row.pop("testing")

    assert fs.testing_mode(db_with([row]), 16) is False
    assert fs.agent_enabled(db_with([row]), 16) is True


# --- куда реально уходит эскалация -----------------------------------------------------------

def test_escalation_goes_to_the_working_group_by_default(ctx, monkeypatch):
    monkeypatch.setattr(ctx.funnel_scenario, "testing_mode", lambda db, fid: False)

    assert ctx.iu_group_dialog_id() == ctx.IU_GROUP_DIALOG_ID


def test_escalation_goes_to_the_test_group_while_testing(ctx, monkeypatch):
    monkeypatch.setattr(ctx.funnel_scenario, "testing_mode", lambda db, fid: True)

    assert ctx.iu_group_dialog_id() == ctx.IU_TEST_GROUP_DIALOG_ID
    assert ctx.IU_TEST_GROUP_DIALOG_ID != ctx.IU_GROUP_DIALOG_ID


def test_unreadable_flag_keeps_the_question_in_the_working_group(ctx, monkeypatch):
    """Молчаливый увод вопроса живого клиента в тестовую группу — худший исход, чем лишний шум."""
    def boom(db, fid):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(ctx.funnel_scenario, "testing_mode", boom)

    assert ctx.iu_group_dialog_id() == ctx.IU_GROUP_DIALOG_ID


def test_notify_sends_the_card_to_the_test_group_while_testing(ctx, monkeypatch):
    """Проверка сквозная: карточка вопроса реально уходит в тестовый чат."""
    sent = {}

    def fake_call(method, params):
        sent.update({"method": method, **params})
        return {"result": 777}

    monkeypatch.setattr(ctx.funnel_scenario, "testing_mode", lambda db, fid: True)
    monkeypatch.setattr(ctx, "_crm_call", fake_call)

    res = ctx.tool_notify_iu_group({"text": "Вопрос клиента без ответа в базе"})

    assert res["sent"] is True
    assert res["dialog_id"] == ctx.IU_TEST_GROUP_DIALOG_ID
    assert sent["DIALOG_ID"] == ctx.IU_TEST_GROUP_DIALOG_ID
    assert sent["BOT_ID"] == ctx.IU_AGENT_BOT_ID, "автор карточки не меняется"


def test_explicit_dialog_id_wins_over_the_flag(ctx, monkeypatch):
    """Служебные вызовы адресуются явно и не должны зависеть от галочки."""
    monkeypatch.setattr(ctx.funnel_scenario, "testing_mode", lambda db, fid: True)
    monkeypatch.setattr(ctx, "_crm_call", lambda method, params: {"result": 778})

    res = ctx.tool_notify_iu_group({"text": "служебное", "dialog_id": "chat2424"})

    assert res["dialog_id"] == "chat2424"
