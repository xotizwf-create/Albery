"""Состав tools/list у пер-агентного коннектора — один источник правды для ворот.

19.08.2026 главный агент перешёл на ЯДРО инструментов (B24_CORE_AGENTS), коннектор стал
объявлять ядро вместо полного набора, а deploy_smoke продолжил ждать полный: ворота
покраснели на здоровом агенте и перестали ловить настоящую поломку. Здесь закреплено, что
ожидание собирается из ТЕХ ЖЕ частей, что и боевая выдача, а не из отдельной копии состава.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture()
def center(app_module):
    importlib.import_module("app")
    import agent_center

    return agent_center


def _agent(slug: str, tools: list[str]):
    return {"slug": slug, "tools_mode": "custom", "tools": tools, "tools_customized": True}


def test_full_mode_agent_advertises_everything_it_has(center, monkeypatch):
    monkeypatch.setattr(center, "_core_mode_agents", lambda: set())
    agent = _agent("main", ["search_tasks", "create_bitrix_task"])

    advertised = center.advertised_tool_names(agent)

    assert advertised == center._agent_tool_names(agent) | center._agent_self_tool_names(agent)
    assert "find_tool" not in advertised


def test_core_mode_agent_advertises_core_plus_the_two_meta_tools(center, monkeypatch):
    from mcp.context_server import CORE_TOOL_NAMES, META_TOOL_SPECS

    from mcp.tool_policy import REVIEWED_TOOL_NAMES, SELF_TOOL_NAMES

    monkeypatch.setattr(center, "_core_mode_agents", lambda: {"main"})
    # Берём заведомо внеядерный инструмент: у агента с пустым списком остаётся базовый
    # набор, и он целиком внутри ядра — на нём проверка была бы пустой.
    outside = sorted(set(REVIEWED_TOOL_NAMES) - set(CORE_TOOL_NAMES) - set(SELF_TOOL_NAMES))
    assert outside, "в реестре не осталось инструментов вне ядра — проверять нечего"
    agent = _agent("main", [outside[0]])

    advertised = center.advertised_tool_names(agent)
    regular = center._agent_tool_names(agent)

    assert set(META_TOOL_SPECS) <= advertised
    assert advertised & regular == regular & set(CORE_TOOL_NAMES)
    # Инструмент вне ядра в списке не объявляется, но остаётся доступным через find_tool.
    assert outside[0] in regular
    assert outside[0] not in advertised


def test_self_tools_are_never_cut_by_the_core(center, monkeypatch):
    monkeypatch.setattr(center, "_core_mode_agents", lambda: {"main"})
    agent = _agent("main", [])

    assert center._agent_self_tool_names(agent) <= center.advertised_tool_names(agent)


def test_expectation_matches_what_the_connector_actually_returns(center, monkeypatch):
    """Анти-дрейф: ожидание сверяется с реальной сборкой списка, а не с числом."""
    from mcp.context_server import list_tools

    monkeypatch.setattr(center, "_core_mode_agents", lambda: {"main"})
    agent = _agent("main", [])
    regular = center._agent_tool_names(agent)

    from_connector = {tool["name"] for tool in list_tools(regular, core=True)}
    from_connector |= center._agent_self_tool_names(agent)

    assert center.advertised_tool_names(agent) == from_connector


def test_smoke_asks_the_contract_instead_of_keeping_its_own_copy():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "scripts" / "deploy_smoke.py").read_text(
        encoding="utf-8"
    )

    assert "advertised_tool_names(agent)" in source
    # Своей арифметики состава в воротах быть не должно — иначе она снова разойдётся.
    assert "CORE_TOOL_NAMES" not in source
    # Расхождение обязано называть имена: одни счётчики стоили часа разбора.
    assert "нет=" in source and "лишние=" in source
