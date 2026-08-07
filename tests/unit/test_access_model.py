"""Модель доступа: человек — пускать или нет, агент — свой набор инструментов.

Правка 07.08.2026 по требованию владельца: «у нас есть базовый набор инструментов, который
должен быть у любого агента, есть максимальный набор — вот в чём отличия. Человеку выдаётся
доступ, а возможности агента зависят от инструментов и инструкций, которые к нему подключены».

Каждый тест здесь закрывает конкретное расхождение, которое было в системе до правки:
  * уровень человека из четырёх значений НИЧЕГО не решал — с включённым UNIVERSAL_MAIN_AGENT
    все допущенные ходили через один коннектор и получали один набор;
  * набор не настроенного агента брался из колонки tier — «Агент Менеджер МП» молча получил
    134 инструмента, которых ему никто не выбирал;
  * набор главного агента содержал семь инструментов, которые раньше отсекал уровень.
"""
from __future__ import annotations

import pytest

import agent_center as ac


# --- Базовый и максимальный набор ------------------------------------------------------

def test_bazovyy_nabor_est_u_lyubogo_agenta():
    """Режим 'base' даёт ровно базовый набор — не пусто и не пресет."""
    agent = {"slug": "novyy", "tools_mode": "base", "tools": [], "tools_customized": False}
    tools = ac._agent_tool_names(agent)
    assert tools == ac.BASE_AGENT_TOOLS, (
        "агент в базовом режиме обязан получить базовый набор целиком: без него он не может "
        "прочитать даже собственные инструкции"
    )


def test_maksimalnyy_nabor_eto_ves_reestr():
    """Режим 'max' отдаёт весь реестр, включая инструменты, добавленные позже."""
    from mcp.context_server import TOOLS
    agent = {"slug": "razrab", "tools_mode": "max", "tools": [], "tools_customized": False}
    assert ac._agent_tool_names(agent) == set(TOOLS)


def test_nastroennyy_nabor_vsegda_soderzhit_bazovyy():
    """Явный список владельца дополняется базовым набором, а не заменяет его."""
    agent = {"slug": "yurist", "tools_mode": "custom",
             "tools": ["search_tasks"], "tools_customized": True}
    tools = ac._agent_tool_names(agent)
    assert "search_tasks" in tools
    assert ac.BASE_AGENT_TOOLS <= tools


def test_nastroennyy_nabor_ne_puskaet_nesushchestvuyushchee():
    """Имя, которого нет в реестре, молча отбрасывается — набор не может выйти за реестр."""
    agent = {"slug": "yurist", "tools_mode": "custom",
             "tools": ["search_tasks", "nesushchestvuyushchiy_instrument"],
             "tools_customized": True}
    assert "nesushchestvuyushchiy_instrument" not in ac._agent_tool_names(agent)


# --- Колонка tier больше не определяет возможности --------------------------------------

@pytest.mark.parametrize("tier", ["faq", "ops", "developer", None, "chto-to-novoe"])
def test_tier_agenta_ne_vliyaet_na_nabor(tier):
    """При заданном режиме колонка tier не меняет набор ни на один инструмент.

    До правки именно она и решала: тот же агент с tier='ops' получал 134 инструмента,
    а с tier='faq' — 18, при полностью одинаковой настройке.
    """
    base_agent = {"slug": "agent", "tools_mode": "custom",
                  "tools": ["search_tasks"], "tools_customized": True, "tier": tier}
    assert ac._agent_tool_names(base_agent) == ac.BASE_AGENT_TOOLS | {"search_tasks"}


def test_stroka_starshe_migratsii_vedet_sebya_po_staromu():
    """Агент без режима (база до миграции 082) сохраняет ПРЕЖНИЙ набор.

    Это защита выкладки: код уезжает на прод раньше миграции, и в этот момент поведение
    живого агента не имеет права измениться.
    """
    from mcp.context_server import OPS_TOOL_NAMES
    legacy = {"slug": "menedzher", "tier": "ops", "tools": [], "tools_customized": False}
    assert ac._agent_tools_mode(legacy) == "legacy"
    assert ac._agent_tool_names(legacy) == set(OPS_TOOL_NAMES) | ac.BASE_AGENT_TOOLS


# --- Клиентский агент остаётся с нулём инструментов -------------------------------------

def test_strogiy_klientskiy_agent_ostaetsya_bez_instrumentov(monkeypatch):
    """Кап манифеста сильнее любого режима, и базовый набор его не пробивает.

    Недоверенный текст клиента не должен получить даже поиск по базе знаний: у клиентского
    рантайма ровно ноль инструментов, и это единственная причина, по которой инъекция в
    промпт ничего не может сделать.
    """
    monkeypatch.setattr(ac, "_agent_manifest_tool_cap", lambda agent: set())
    for mode in ("base", "max", "custom"):
        agent = {"slug": "iu-customer-runtime", "tools_mode": mode,
                 "tools": ["search_tasks"], "tools_customized": True}
        assert ac._agent_tool_names(agent) == set(), f"режим {mode} пробил кап манифеста"


# --- Доступ человека — булев ------------------------------------------------------------

def test_dostup_cheloveka_bulev(monkeypatch):
    """Любое значение кроме 'none' означает «доступ есть». Уровней больше нет."""
    import b24bot
    monkeypatch.setattr(b24bot, "_agent_access_map",
                        lambda: {16: "admin", 22: "ops", 30: "faq", 40: "none"})
    assert b24bot._b24_has_access(16) is True
    assert b24bot._b24_has_access(22) is True
    assert b24bot._b24_has_access(30) is True, (
        "исторический уровень 'faq' — это доступ, а не запрет: запрет только 'none'"
    )
    assert b24bot._b24_has_access(40) is False
    assert b24bot._b24_has_access(999) is True, "строки нет — это не запрет"


def test_v_b24bot_ne_ostalos_resheniy_po_urovnyu():
    """В коде бота не должно остаться ветвлений по уровню доступа человека.

    Тест не про красоту: пока такие ветвления живы, интерфейс обещает уровни, которых
    поведение не даёт, и следующий человек снова будет чинить это расхождение.
    """
    from pathlib import Path
    src = Path(b24bot_path()).read_text(encoding="utf-8")
    for pattern in ('tier == "admin"', 'tier == "ops"', 'tier == "faq"',
                    'tier in ("admin", "ops")', 'tier in ("ops", "admin")'):
        assert pattern not in src, f"в b24bot.py осталось решение по уровню: {pattern}"


def b24bot_path() -> str:
    import b24bot
    return b24bot.__file__


# --- Набор главного агента --------------------------------------------------------------

def test_glavnyy_agent_bez_opasnykh_instrumentov():
    """Миграция 082 обязана убирать из набора main семь инструментов, которые раньше
    отсекал уровень человека. Проверяем сам текст миграции: на живой базе это единственное
    место, где решение зафиксировано."""
    from pathlib import Path
    migration = Path(__file__).resolve().parents[2] / "database" / "migrations" / "082_agent_tools_mode.sql"
    sql = migration.read_text(encoding="utf-8")
    for tool in ("delete_bitrix_task", "delete_crm_deal", "delete_crm_pipeline",
                 "send_telegram_message", "list_telegram_contacts",
                 "list_dialog_errors", "resolve_dialog_errors"):
        assert tool in sql, f"миграция не убирает {tool} из набора главного агента"
    assert "slug = 'main'" in sql
