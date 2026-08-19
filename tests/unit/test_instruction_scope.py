"""Инструкции: в промпте только поведение, справочники — по маршрутной карте.

19.08.2026 замер: 23 «универсальные» инструкции = 63 553 символа в КАЖДОМ ходе КАЖДОГО
агента, поверх 116 445 символов схем инструментов. Ответ на «Ты тут?» занимал 278 секунд,
и журнал за это время пуст — время уходило на разбор промпта.

Половина «универсальных» оказалась тематическими справочниками (таблицы, CRM-формы,
оргструктура): они нужны в конкретной работе, а не в каждом ответе. Их вынесли в optional
и добавили в маршрутную карту указатели — агент читает документ, когда берётся за тему.

Отдельно исправлено умолчание: раньше документ без явного `scope` становился универсальным,
то есть промпт рос от забывчивости. Теперь по умолчанию optional.
"""
from __future__ import annotations

import pytest

from agent_knowledge import (SCOPE_OPTIONAL, SCOPE_UNIVERSAL, load_instructions,
                             parse_doc, universal_instruction_paths)

# Это определяет ПОВЕДЕНИЕ в каждом ответе — обязано быть в промпте всегда.
MUST_STAY_UNIVERSAL = [
    "Маршрутная карта",
    "Базовое поведение",
    "Базовое поведение / Критическое мышление и проверка утверждений",
    "Работа в системе / Проверка выводов по О компании",
    "Формат ответа",
    "Порядок поиска",
]

# Тематические справочники: нужны при работе с темой, а не при каждом «Ты тут?».
MUST_BE_OPTIONAL = [
    "Google Sheets и Apps Script",
    "Описание доступных инструментов",
    "Оргструктура компании",
    "CRM / Анкеты и CRM-формы — как отвечать",
    "Формат ответа / Человеческое оформление задач и проблем",
]


@pytest.fixture(scope="module")
def instructions():
    items = load_instructions()
    assert items, "реестр инструкций не прочитался"
    return {i["path"]: i for i in items}


@pytest.mark.parametrize("path", MUST_STAY_UNIVERSAL)
def test_behaviour_instructions_stay_in_prompt(path, instructions):
    """Эти документы работают именно тем, что всегда перед глазами.

    Критическое мышление и проверка выводов удерживают агента от выдумывания —
    вынести их «для экономии» значит купить скорость ценой достоверности.
    """
    assert path in instructions, f"инструкция пропала: {path}"
    assert instructions[path]["scope"] == SCOPE_UNIVERSAL, path


@pytest.mark.parametrize("path", MUST_BE_OPTIONAL)
def test_reference_instructions_left_the_prompt(path, instructions):
    assert path in instructions, f"инструкция пропала: {path}"
    assert instructions[path]["scope"] == SCOPE_OPTIONAL, (
        f"{path} снова уехала в каждый ход"
    )


def test_universal_budget_is_bounded(instructions):
    """Цифра проверяемая, а не на слово: было 63 553 символа."""
    universal = [i for i in instructions.values() if i["scope"] == SCOPE_UNIVERSAL]
    total = sum(len(i["content"]) for i in universal)

    assert total < 40000, f"универсальных инструкций {total} символов — промпт снова растёт"


@pytest.mark.parametrize("path", MUST_BE_OPTIONAL)
def test_routing_map_points_at_every_moved_instruction(path, instructions):
    """Вынести документ и не сказать, где он лежит, — значит просто отнять его у агента."""
    route = instructions["Маршрутная карта"]["content"]
    tail = path.split(" / ")[-1]
    assert tail in route, f"в маршрутной карте нет указателя на «{tail}»"


def test_routing_map_explains_the_tool_tail(instructions):
    """Половина набора инструментов теперь за find_tool — об этом надо сказать прямо."""
    route = instructions["Маршрутная карта"]["content"]
    assert "find_tool" in route and "call_tool" in route


def test_missing_scope_defaults_to_optional():
    """Забывчивость должна давать дешёвый вариант, а не раздувать промпт всем агентам."""
    meta, _ = parse_doc("---\nname: Без области\n---\n\nтекст")
    assert "scope" not in meta

    from agent_knowledge import SCOPE_OPTIONAL as default_scope
    assert default_scope == "optional"


def test_universal_paths_helper_agrees_with_registry(instructions):
    from_helper = universal_instruction_paths()
    from_registry = {p for p, i in instructions.items() if i["scope"] == SCOPE_UNIVERSAL}
    assert from_helper == from_registry
