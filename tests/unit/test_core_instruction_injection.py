"""Правило, которого модель не видит каждый ход, — это не правило.

29.07.2026 выяснилось дважды подряд. Сначала с инструментами: read/edit Google-документов
существовали, но лежали вне CORE_TOOL_NAMES, и агент по-прежнему говорил «нет доступа».
Затем с инструкциями: «Вопросы о возможностях и доступе» — та самая инструкция, которая
предписывала фразу «у вас не тот уровень прав, обратитесь к Александру», — в постоянный
инжект НЕ входила (`_B24_CORE_INSTR_NAMES` берёт только пять блоков). Значит и исправленная
версия доезжала бы до модели лишь при явном вызове get_ai_instructions.

Инжект ограничен капом, поэтому список блоков — осознанный выбор, а не «добавить всё».
Этот тест удерживает в нём то, что определяет ЧЕСТНОСТЬ ответов.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def bot():
    import b24bot

    return b24bot


FAKE_ROWS = [
    {"name": "Маршрутная карта", "content": "маршрут"},
    {"name": "Порядок поиска", "content": "поиск"},
    {"name": "Формат ответа", "content": "формат"},
    {"name": "Базовое поведение", "content": "база"},
    {"name": "Критическое мышление и проверка утверждений", "content": "критика"},
    {"name": "Вопросы о возможностях и доступе", "content": "ПРАВИЛО ЧЕСТНОСТИ ПРО ДОСТУП"},
    {"name": "Ежедневный отчет по компании", "content": "не должен попадать в инжект"},
]


def _injected(bot, monkeypatch):
    import mcp.context_server as ctx

    monkeypatch.setattr(ctx, "load_ai_instructions", lambda *a, **kw: FAKE_ROWS)
    monkeypatch.setitem(bot._B24_CORE_INSTR_CACHE, "at", 0.0)
    monkeypatch.setitem(bot._B24_CORE_INSTR_CACHE, "text", "")
    monkeypatch.setenv("B24_INJECT_CORE_INSTR", "1")
    return bot._b24_core_instructions()


class TestCoreInstructionInjection:
    def test_capability_and_access_rules_reach_the_model_every_turn(self, bot, monkeypatch):
        text = _injected(bot, monkeypatch)
        assert "ПРАВИЛО ЧЕСТНОСТИ ПРО ДОСТУП" in text, (
            "инструкция про возможности и доступ обязана быть в постоянном инжекте — "
            "именно она определяет, как агент объясняет отказ"
        )

    def test_injection_stays_selective(self, bot, monkeypatch):
        text = _injected(bot, monkeypatch)
        assert "не должен попадать в инжект" not in text  # кап на инжект — не свалка

    def test_kill_switch_still_works(self, bot, monkeypatch):
        monkeypatch.setenv("B24_INJECT_CORE_INSTR", "0")
        assert bot._b24_core_instructions() == ""
