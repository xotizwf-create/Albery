"""Настраиваемый сценарий воронки: владелец правит текст шага и может остановить агента.

Владелец 25.07.2026: «сделаем инструмент „Работа с воронками“ … чтобы этим можно было прям
управлять». Здесь проверяется главное: настройка из кабинета РЕАЛЬНО доходит до агента, а сбой
базы или отсутствие настройки не ломают работу — тогда действует сценарий из кода.
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


def setup_function(_):
    fs.invalidate()


def test_owner_text_replaces_the_code_step():
    """Правка из кабинета обязана доходить до агента — иначе инструмент бесполезен."""
    db = db_with([{"funnel_id": 16, "stage_id": "C16:CONTACTED", "trigger": "ответили",
                   "need": "подтверждение анкеты",
                   "action": "Спроси мягко, всё ли верно в анкете.", "enabled": True}])

    step = fs.step_override(db, "C16:CONTACTED")

    assert step["action"] == "Спроси мягко, всё ли верно в анкете."
    assert step["need"] == "подтверждение анкеты"


def test_empty_fields_fall_back_to_code():
    """Пустое поле = «как в коде»: владелец всегда может откатиться, не зовя инженера."""
    db = db_with([{"funnel_id": 16, "stage_id": "C16:CONTACTED", "trigger": "", "need": "",
                   "action": "Только это переопределяем.", "enabled": True}])

    step = fs.step_override(db, "C16:CONTACTED")

    assert set(step) == {"action"}, "пустые поля не считаются настройкой"


def test_stage_without_settings_uses_code():
    assert fs.step_override(db_with([]), "C16:NEW") == {}


def test_stage_of_another_funnel_is_not_confused():
    """Этап сам говорит, к какой воронке относится: настройки воронки 16 не влияют на воронку 2."""
    db = db_with([{"funnel_id": 16, "stage_id": "C16:NEW", "trigger": "", "need": "",
                   "action": "текст воронки 16", "enabled": True}])

    assert fs.step_override(db, "C2:NEW") == {}
    assert fs.step_override(db, "C16:NEW")["action"] == "текст воронки 16"


def test_agent_can_be_stopped_from_the_cabinet():
    """Выключатель — это способ владельца остановить автоответы без инженера."""
    db = db_with([{"funnel_id": 16, "stage_id": "", "trigger": "", "need": "", "action": "",
                   "enabled": False}])

    assert fs.agent_enabled(db, 16) is False
    assert fs.agent_enabled(db, 2) is True, "другие воронки не задеты"


def test_agent_is_enabled_by_default():
    """Пока владелец ничего не трогал, поведение прежнее — агент работает."""
    assert fs.agent_enabled(db_with([]), 16) is True


def test_database_failure_never_stops_the_agent():
    """Сбой базы не имеет права ни менять сценарий, ни выключать агента."""
    @contextlib.contextmanager
    def broken():
        raise RuntimeError("база недоступна")
        yield  # pragma: no cover

    assert fs.step_override(broken, "C16:NEW") == {}
    assert fs.agent_enabled(broken, 16) is True


def test_settings_are_cached_but_invalidate_after_save():
    """Читается на каждом ходу — значит кэш; но после сохранения из кабинета он сбрасывается."""
    calls = []

    @contextlib.contextmanager
    def counting_db():
        calls.append(1)
        yield FakeConn([{"funnel_id": 16, "stage_id": "C16:NEW", "trigger": "", "need": "",
                         "action": "версия 1", "enabled": True}])

    fs.step_override(counting_db, "C16:NEW")
    fs.step_override(counting_db, "C16:NEW")
    assert len(calls) == 1, "второй ход не должен снова читать базу"

    fs.invalidate()
    fs.step_override(counting_db, "C16:NEW")
    assert len(calls) == 2, "после правки в кабинете настройка подхватывается сразу"


def test_step_block_prefers_the_owner_text(monkeypatch):
    """Сквозная проверка: блок шага, который уходит агенту в промпт, содержит текст владельца."""
    import tg_agent as tg
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "get_crm_deal", {"handler": lambda a: {
        "deal": {"deal_id": 148, "stage_id": "C16:UC_ANKETA", "custom_fields": {}}}})
    monkeypatch.setattr(tg, "funnel_scenario", fs)
    monkeypatch.setattr(fs, "step_override",
                        lambda db, stage: {"action": "Скажи: «Рад, что всё сошлось!»"})

    block = tg.funnel_step_block(148)

    assert "Рад, что всё сошлось" in block
    assert "ТЕКУЩИЙ ШАГ ВОРОНКИ" in block, "оболочка шага остаётся на месте"
