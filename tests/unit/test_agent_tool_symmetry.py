"""Замороженный набор инструментов агента не должен уметь «создать», но не «изменить».

Жалоба владельца 30.07.2026, диалог 16, ход 1325. Инструменты read_google_doc/edit_google_doc
были добавлены, попали в реестр, в CORE и в OPS, живая служба отдавала их на всех коннекторах
— и агент всё равно ответил: «Напрямую редактировать существующий Google Документ по ссылке я
сейчас не умею: в текущем наборе нет действия для записи изменений в него».

Причина глубже реестра. У агента `main` (как и у finansist, sklad, po-rabote-s-iu,
albery-ai-bot) `tools_customized=true` и tools — ЗАМОРОЖЕННЫЙ белый список из 115 имён,
собранный до появления новых инструментов. `_agent_tool_names` — жёсткие ворота: tools/list
по коннектору отдаёт ровно этот список, поэтому нового инструмента для модели не существует.
Во всех пяти списках `create_google_doc` был, а read/edit — нет.

Правило: набор, в котором есть «создать», обязан содержать «прочитать» и «изменить» — иначе
агент застревает на полпути и объясняет это пользователю как отсутствие возможности.
"""
from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def center():
    import agent_center

    return agent_center


def frozen_agent(tools, tier="ops", slug="main"):
    return {"slug": slug, "tier": tier, "tools_customized": True, "tools": list(tools)}


class TestCompanionClosure:
    def test_create_doc_brings_read_and_edit(self, center):
        served = center._agent_tool_names(frozen_agent(["create_google_doc"]))
        assert {"read_google_doc", "edit_google_doc"} <= served, (
            "агент, умеющий создать документ, обязан уметь его прочитать и изменить"
        )

    def test_create_sheet_brings_read_and_write(self, center):
        served = center._agent_tool_names(frozen_agent(["create_google_sheet"]))
        assert {"read_google_sheet_values", "write_google_sheet_values",
                "get_google_sheet_meta"} <= served

    def test_real_main_whitelist_from_prod_gets_the_missing_pair(self, center):
        """Реальный случай: в списке есть создание документа и работа с таблицами, нет read/edit."""
        served = center._agent_tool_names(frozen_agent([
            "create_google_doc", "create_google_sheet", "write_google_sheet_values",
            "search_company_knowledge", "search_tasks",
        ]))
        assert "edit_google_doc" in served
        assert "read_google_doc" in served

    def test_agent_without_doc_tools_gets_nothing_extra(self, center):
        """Замыкание не раздаёт права: нет «создать» — не появляется и «изменить»."""
        served = center._agent_tool_names(frozen_agent(["search_tasks", "get_org_structure"]))
        assert "edit_google_doc" not in served
        assert "create_google_doc" not in served

    def test_closure_never_escapes_the_manifest_cap(self, center, monkeypatch):
        """Строгие клиентские агенты капаются манифестом — замыкание не может его расширить."""
        monkeypatch.setattr(center, "_agent_manifest_tool_cap",
                            lambda agent: {"create_google_doc", "search_company_knowledge"})
        served = center._agent_tool_names(frozen_agent(["create_google_doc"], slug="iu-customer-runtime"))
        assert "edit_google_doc" not in served
        assert served <= {"create_google_doc", "search_company_knowledge"}

    def test_only_registry_names_survive(self, center):
        served = center._agent_tool_names(frozen_agent(["create_google_doc", "нет_такого_инструмента"]))
        assert "нет_такого_инструмента" not in served
