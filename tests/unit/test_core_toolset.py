"""Ядро инструментов: агент носит с собой рабочий набор, остальное достаёт по требованию.

19.08.2026 владелец: «промт 182к символов слишком много». Замер подтвердил: схемы 111
инструментов — 116 445 символов в КАЖДОМ ходе, плюс 66 218 символов универсальных
инструкций. Ответ на «Ты тут?» занял 278 секунд, и журнал за это время пуст — время
уходило не на работу и не на сеть, а на разбор промпта.

Состав ядра собран по ФАКТИЧЕСКОЙ статистике вызовов главного агента за месяц
(/root/.hermes/state.db): верхние 16 позиций дают 85% обращений. Остальное не пропадает —
имена объявляются списком и достаются через find_tool/call_tool.

Отдельный принцип: группы держим ЦЕЛИКОМ, даже если хвост группы вызывается редко.
29.07.2026 у агента было «создать документ» без «изменить», и он отвечал пользователям,
что не имеет доступа, хотя доступ был. Неполный набор хуже лишних символов.
"""
from __future__ import annotations

import json

import pytest

from mcp import context_server as cs
from mcp.tool_policy import REVIEWED_TOOL_NAMES

# Самые частые вызовы главного агента за месяц — эти обязаны быть под рукой.
HEAVILY_USED = [
    ("start_here_always_read_ai_instructions", 1020),
    ("search_tasks", 479),
    ("fetch_url", 330),
    ("search_company_knowledge", 301),
    ("read_google_sheet_values", 234),
    ("get_task_comments", 141),
    ("get_bitrix_bot_chat", 139),
    ("create_bitrix_task", 129),
    ("write_google_sheet_values", 126),
    ("get_wb_prices", 110),
    ("get_org_structure", 106),
    ("get_company_file", 97),
    ("get_zoom_call_transcript", 84),
    ("get_google_sheet_meta", 82),
    ("manage_apps_script", 80),
    ("get_attachment_text", 74),
]


@pytest.mark.parametrize("name,calls", HEAVILY_USED)
def test_heavily_used_tools_stay_in_core(name, calls):
    """Гонять частый инструмент через find_tool дороже, чем держать его в промпте."""
    assert name in cs.CORE_TOOL_NAMES, f"{name} вызывался {calls} раз за месяц"


def test_verification_tool_is_in_core_even_though_rarely_called():
    """Статистика тут врёт: инструмент новый, но инструкция требует его перед «готово».

    Без него агент отчитается о сломанной таблице как о готовой — случай 17.08.2026,
    когда сводка показывала 0 рублей при живых данных.
    """
    assert "check_google_sheet_health" in cs.CORE_TOOL_NAMES


@pytest.mark.parametrize("group", [
    ("create_bitrix_task", "update_bitrix_task", "add_bitrix_task_comment",
     "complete_bitrix_task"),
    ("create_google_sheet", "read_google_sheet_values", "write_google_sheet_values"),
    ("create_google_doc", "read_google_doc", "edit_google_doc"),
    ("create_recurring_task", "list_recurring_tasks", "delete_recurring_task"),
])
def test_capability_groups_stay_whole(group):
    """Умеешь создать — умей прочитать и изменить.

    Половина набора приводит к тому, что агент заявляет об отсутствии доступа вместо работы
    (инцидент 29.07.2026 с документами).
    """
    missing = [name for name in group if name not in cs.CORE_TOOL_NAMES]
    assert not missing, f"группа разорвана, не хватает: {missing}"


def test_core_is_actually_small():
    """Прежнее ядро на 57 инструментов экономило лишь 38% — это не решало задачу."""
    assert len(cs.CORE_TOOL_NAMES) <= 45, (
        f"в ядре {len(cs.CORE_TOOL_NAMES)} инструментов — оно снова распухло"
    )


def test_core_names_are_real_reviewed_tools():
    """Опечатка в имени = инструмент молча выпал из ядра и уехал в хвост."""
    unknown = sorted(cs.CORE_TOOL_NAMES - set(REVIEWED_TOOL_NAMES))
    assert not unknown, f"в ядре имена, которых нет в реестре: {unknown}"


def _schema_chars(names) -> int:
    total = 0
    for name in names:
        spec = cs.TOOLS.get(name) or cs.META_TOOL_SPECS.get(name)
        if spec:
            total += len(json.dumps({"name": name, "description": spec.get("description", ""),
                                     "inputSchema": spec.get("inputSchema", {})},
                                    ensure_ascii=False))
    return total


def test_core_saves_most_of_the_prompt():
    """Ради этого всё и делается — цифра должна быть проверяемой, а не на слово."""
    full = set(REVIEWED_TOOL_NAMES)
    core = (full & cs.CORE_TOOL_NAMES) | set(cs.META_TOOL_SPECS)
    saved = _schema_chars(full) - _schema_chars(core)

    assert saved > 100000, f"экономия всего {saved} символов — мало"


def test_hidden_tools_remain_reachable():
    """Урезание не должно ничего отнимать: хвост достаётся через find_tool/call_tool."""
    assert "find_tool" in cs.META_TOOL_SPECS
    assert "call_tool" in cs.META_TOOL_SPECS


def test_core_mode_is_per_agent_not_global(monkeypatch):
    """Включаем по одному агенту и смотрим замер; кроны остаются на полных наборах."""
    import agent_center

    monkeypatch.setenv("B24_CORE_AGENTS", "main, agent-sklad")
    assert agent_center._core_mode_agents() == {"main", "agent-sklad"}

    monkeypatch.setenv("B24_CORE_AGENTS", "")
    assert agent_center._core_mode_agents() == set(), "пустая переменная = ядро выключено всем"


def test_list_tools_core_mode_returns_only_core():
    full = {"search_tasks", "fetch_url", "delete_crm_pipeline", "list_crm_forms"}
    names = {t["name"] for t in cs.list_tools(full, core=True)}

    assert "search_tasks" in names
    assert "delete_crm_pipeline" not in names, "редкий инструмент обязан уйти в хвост"
