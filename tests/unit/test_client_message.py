"""Человеческая оболочка вокруг сообщений, которые отправляет КОД (владелец, 25.07.2026).

«Агент даже не поздоровался с клиентом, ну где вежливость». Замер по проду за сутки: первым
сообщением человеку уходило голое «Вижу анкету: • Ссылка на магазин…» (диалог 256942600) и
«Уточню это у команды и вернусь с ответом» (диалог 1451982360). Оба — от кода, мимо модели.

Дословность фактов при этом остаётся неприкосновенной: оболочка добавляет текст ВОКРУГ, но
никогда внутрь.
"""
from __future__ import annotations

import json

import pytest

import client_message as cm

DOC = """Условия ИУ — текст для клиента

--- ТЕКСТ КЛИЕНТУ ---

Индивидуальные условия снижают комиссию до 12%.

Стоимость — 30 000 ₽ в месяц."""


def test_greeting_uses_the_name_when_it_is_known():
    assert cm.greeting_for("Александр") == "Здравствуйте, Александр!"
    assert cm.greeting_for("  Александр  ") == "Здравствуйте, Александр!"
    assert cm.greeting_for("") == "Здравствуйте!"


def test_first_contact_message_starts_with_a_greeting():
    """Ровно то, чего не было: первое сообщение обязано начинаться с приветствия."""
    msg = cm.compose("Вижу анкету:\n\n• Категории товара — одежда\n\nВсё верно?",
                     name="Александр", greet=True, lead_in=cm.LEAD_IN_ANKETA)

    assert msg.startswith("Здравствуйте, Александр!")
    assert "Спасибо, анкету получил" in msg, "человек видит признание, а не голый блок"
    assert msg.rstrip().endswith("Всё верно?")


def test_no_greeting_when_the_conversation_already_started():
    """Здороваться в каждом сообщении — тоже не по-человечески."""
    msg = cm.compose("Вижу анкету:\n\n• Категории — одежда", name="Александр", greet=False)

    assert "Здравствуйте" not in msg
    assert msg.startswith("Вижу анкету:")


def test_verbatim_block_survives_the_envelope():
    """Оболочка добавляет ВОКРУГ, но не внутрь: условия остаются словом в слово."""
    msg = cm.compose(DOC, name="Пётр", greet=True, lead_in=cm.LEAD_IN_TERMS,
                     follow_up="Есть вопросы по условиям?")

    assert cm.verbatim_intact(msg, DOC)
    assert msg.index("Здравствуйте") < msg.index("Индивидуальные условия")
    assert msg.rstrip().endswith("Есть вопросы по условиям?")


def test_changed_verbatim_is_caught():
    """Если факт всё-таки переписали — это обязано быть видно проверкой, а не клиенту."""
    assert not cm.verbatim_intact("Здравствуйте!\n\nКомиссия примерно 12%", DOC)


def test_empty_parts_do_not_leave_holes():
    msg = cm.compose("Текст", greet=False)

    assert msg == "Текст"


# --- то же самое, но через настоящие пути отправки ------------------------------------------

@pytest.fixture
def tg(monkeypatch, tmp_path):
    import tg_agent

    from mcp import context_server as cs

    state = tmp_path / "state.json"
    state.write_text(json.dumps({
        "contacts": {"alexxandrn": {"id": 555, "username": "alexxandrn", "name": "Александр"}},
    }), encoding="utf-8")
    monkeypatch.setattr(tg_agent, "STATE_PATH", state)
    monkeypatch.setattr(tg_agent, "load_state",
                        lambda: json.loads(state.read_text(encoding="utf-8")))
    monkeypatch.setattr(tg_agent, "save_state", lambda s: None)
    monkeypatch.setitem(cs.TOOLS, "list_company_files",
                        {"handler": lambda a: {"files": [{"name": "Условия ИУ — текст для клиента",
                                                          "google_file_id": "doc-1"}]}})
    monkeypatch.setitem(cs.TOOLS, "get_company_file", {"handler": lambda a: {"content": DOC}})
    monkeypatch.setattr(tg_agent, "journal", lambda *a, **k: None)
    monkeypatch.setattr(tg_agent, "_mark_terms_sent", lambda uid: None)
    return tg_agent


def test_terms_reach_the_client_with_a_greeting_and_word_for_word(tg, monkeypatch):
    """Живой случай (диалог 764181402): документ на 2084 символа ушёл сырым дампом."""
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda uid: 0)      # первый контакт

    tg.send_terms(0, 555)

    assert sent[0].startswith("Здравствуйте, Александр!")
    assert "Индивидуальные условия снижают комиссию до 12%." in sent[0], "документ дословно"
    assert sent[0].rstrip().endswith(tg.TERMS_QUESTION)


def test_terms_do_not_greet_twice_in_the_same_conversation(tg, monkeypatch):
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda uid: 407)    # уже разговаривали

    tg.send_terms(0, 555)

    assert not sent[0].startswith("Здравствуйте")
    assert "Рассказываю про условия" in sent[0], "подводка нужна и в середине разговора"


def test_service_line_to_a_new_person_is_not_a_naked_phrase(tg, monkeypatch):
    """Диалог 1451982360: первой репликой человек получил «Уточню это у команды»."""
    sent = []
    monkeypatch.setattr(tg, "send_html", lambda uid, html, plain: sent.append(plain) or (True, ""))
    monkeypatch.setattr(tg, "_dialog_out_watermark", lambda uid: 0)
    monkeypatch.setattr(tg, "escalate_to_human", lambda *a, **k: None)

    tg._terms_question_to_humans({"id": 555, "username": "alexxandrn", "first_name": "Александр"},
                                 "а какой у вас ДРР?")

    assert sent[0].startswith("Здравствуйте, Александр!")
    assert tg.TERMS_ASK_HUMAN_REPLY in sent[0]
