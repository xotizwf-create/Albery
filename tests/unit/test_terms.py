"""Условия клиенту — дословно из документа владельца (владелец, 23.07.2026).

Агент пропустил этап условий: сверил анкету и сразу попросил реквизиты. Теперь после
подтверждения данных он обязан отправить условия — и именно СЛОВО В СЛОВО, а не пересказом.
"""
from __future__ import annotations

import pytest

DOC = """Источник: https://docs.google.com/document/d/XXX/edit
Тип: application/vnd.google-apps.document

Условия ИУ — текст для клиента

Как работает этот документ. Всё ниже строки агент отправляет дословно.

--- ТЕКСТ КЛИЕНТУ ---

Индивидуальные условия снижают комиссию до 12% и дают приоритет в выдаче.

Стоимость — 30 000 ₽ в месяц, первый месяц бесплатно."""


@pytest.fixture
def tg(monkeypatch):
    import tg_agent

    def fake_files(_args):
        return {"files": [{"name": "Условия ИУ — текст для клиента", "google_file_id": "doc-1"},
                          {"name": "Шаблон договора ИУ", "google_file_id": "doc-2"}]}

    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "list_company_files", {"handler": fake_files})
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": DOC}})
    return tg_agent


def test_only_the_client_part_is_taken(tg):
    """Инструкция для владельца в начале документа клиенту уходить не должна."""
    body = tg.terms_text()

    assert "Индивидуальные условия снижают комиссию" in body
    assert "Как работает этот документ" not in body
    assert "Источник:" not in body and "Тип:" not in body


def test_text_goes_to_the_client_word_for_word(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append((uid, plain)) or (True, ""))
    monkeypatch.setattr(tg, "journal", lambda *a, **k: None)

    res = tg.send_terms(0, 555)

    uid, text = sent[0]
    assert uid == 555 and res["sent"]
    assert "комиссию до 12%" in text and "30 000 ₽ в месяц" in text
    assert text.endswith("Есть вопросы по условиям?"), "вопрос агент добавляет сам"


def test_unfilled_document_is_never_sent(tg, monkeypatch):
    """Неполные условия у клиента хуже паузы."""
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "get_company_file",
                        {"handler": lambda a: {"content": "--- ТЕКСТ КЛИЕНТУ ---\n[ЗАПОЛНИТЬ] цена"}})
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda *a: sent.append(a) or (True, ""))

    with pytest.raises(ValueError, match="ЗАПОЛНИТЬ"):
        tg.send_terms(0, 555)
    assert sent == [], "клиенту не должно уйти ничего"


def test_missing_document_fails_loudly(tg, monkeypatch):
    from mcp import context_server as cs

    monkeypatch.setitem(cs.TOOLS, "list_company_files", {"handler": lambda a: {"files": []}})

    with pytest.raises(ValueError, match="нет документа"):
        tg.terms_text()


def test_undelivered_terms_are_not_recorded_as_sent(tg, monkeypatch):
    monkeypatch.setattr(tg, "send_html", lambda *a: (False, "чат недоступен"))
    journalled = []
    monkeypatch.setattr(tg, "journal", lambda *a, **k: journalled.append(a))

    with pytest.raises(RuntimeError, match="не отправлены"):
        tg.send_terms(0, 555)
    assert journalled == []


# --- место условий в маршруте воронки -------------------------------------------------------

def test_confirmed_form_leads_to_terms_not_to_requisites(tg):
    """Ровно то, что владелец назвал пропуском: сразу после анкеты просили реквизиты."""
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:CONTACTED", "custom_fields": {}})

    assert "send_terms" in st["action"]
    assert "реквизит" not in st["action"].lower(), "реквизиты — только после условий и вопросов"


def test_terms_step_forbids_retelling(tg):
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149", "custom_fields": {}})

    assert st["step"] == "Отправка условий"
    assert "своими словами" in st["action"].lower()


def test_after_terms_the_agent_answers_questions_and_then_asks_requisites(tg, monkeypatch):
    monkeypatch.setattr(tg, "TERMS_SENT_FIELD", "UF_CRM_TERMS")
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {"UF_CRM_TERMS": "2026-07-23"}})

    assert st["step"] == "Вопросы по условиям"
    assert "search_company_knowledge" in st["action"]
    assert "помня весь разговор" in st["action"], "контекст предыдущих шагов не теряется"
    assert "реквизиты" in st["action"].lower(), "следующий шаг назван прямо"


def test_requisites_already_collected_means_terms_are_behind(tg):
    """Старые сделки без поля-отметки не должны застрять на условиях."""
    st = tg.funnel_next_step({"deal_id": 86, "stage_id": "C16:S84294149",
                              "custom_fields": {tg.CONTRACT_REQUISITES_FIELD: "ИНН 7704123456"}})

    assert st["step"] == "Отправка договора"
