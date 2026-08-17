"""Граница автоотправки: сама уходит только рутина по утверждённым шаблонам.

Владелец выбрал гибрид — шаблонные письма поставщикам уходят без него, всё остальное ждёт
просмотра. Правило живёт в коде, а не в инструкции агента, потому что письмо внешнему
контрагенту не отзывается: 17.08.2026 инструкция «проверяй результат» существовала, агент
её выполнял, и всё равно отдал сломанную таблицу. Там ценой была таблица, здесь — репутация
перед фабрикой.

Реестр шаблонов по умолчанию ПУСТ: пока владелец не назвал типы писем, сама не уходит ни
одна буква. «Разрешено ничего» безопаснее, чем «разрешено на усмотрение модели».
"""
from __future__ import annotations

import pytest

import mail
import mail_policy


@pytest.fixture()
def outbox(monkeypatch):
    sent: list[dict] = []
    drafted: list[dict] = []

    def fake_send(to, subject, body, **kw):
        sent.append({"to": to, "subject": subject, "body": body, **kw})
        return {"message_id": "sent-1", "thread_id": kw.get("thread_id") or "t-1"}

    def fake_draft(to, subject, body, **kw):
        drafted.append({"to": to, "subject": subject, "body": body, **kw})
        return {"draft_id": "draft-1", "message_id": "dm-1"}

    monkeypatch.setattr(mail, "mail_send_raw", fake_send)
    monkeypatch.setattr(mail, "mail_create_draft", fake_draft)
    return sent, drafted


@pytest.fixture()
def approved(monkeypatch):
    monkeypatch.setattr(mail_policy, "APPROVED_TEMPLATES", {
        "первичный_запрос": {
            "required": ["компания", "категория"],
            "subject": "Запрос по категории {{категория}}",
            "body": "Здравствуйте, {{компания}}! Пришлите, пожалуйста, остатки и цены "
                    "по категории {{категория}}.",
        },
    })


def test_nothing_is_sent_while_no_template_is_approved(outbox):
    """Состояние по умолчанию: реестр пуст — значит сама не уходит ни одна буква."""
    assert mail_policy.approved_template_names() == []

    sent, drafted = outbox
    result = mail_policy.send_or_draft("supplier@example.ru", "Тема", "Текст")

    assert result["action"] == "drafted"
    assert sent == []
    assert len(drafted) == 1


def test_free_form_letter_is_always_drafted(outbox, approved):
    """Даже при наличии шаблонов вольный текст уходит только через человека."""
    sent, drafted = outbox
    result = mail_policy.send_or_draft(
        "supplier@example.ru", "Договоримся о цене", "Готовы обсудить скидку 15%")

    assert result["action"] == "drafted"
    assert "не по утверждённому шаблону" in result["reason"]
    assert sent == []


def test_approved_template_goes_out_by_itself(outbox, approved):
    sent, drafted = outbox
    result = mail_policy.send_or_draft(
        "supplier@example.ru", "", "", template="первичный_запрос",
        values={"компания": "ИвановоТрикотаж", "категория": "халаты"})

    assert result["action"] == "sent"
    assert drafted == []
    assert sent[0]["subject"] == "Запрос по категории халаты"
    assert "ИвановоТрикотаж" in sent[0]["body"]


def test_unknown_template_is_drafted_not_sent(outbox, approved):
    """Опечатка в имени шаблона не должна превращаться в вольное письмо поставщику."""
    sent, drafted = outbox
    result = mail_policy.send_or_draft(
        "supplier@example.ru", "", "", template="первичный_запос",  # опечатка
        values={"компания": "X", "категория": "Y"})

    assert result["action"] == "drafted"
    assert sent == []


def test_missing_required_value_blocks_sending(outbox, approved):
    """Письмо без категории бессмысленно для поставщика — и уйти не должно."""
    sent, drafted = outbox
    result = mail_policy.send_or_draft(
        "supplier@example.ru", "", "", template="первичный_запрос",
        values={"компания": "ИвановоТрикотаж"})

    assert result["action"] == "drafted"
    assert "категория" in result["reason"]
    assert sent == []


def test_placeholder_never_reaches_the_supplier(outbox, monkeypatch):
    """Дыра «{{срок}}» в письме контрагенту хуже, чем неотправленное письмо."""
    monkeypatch.setattr(mail_policy, "APPROVED_TEMPLATES", {
        "напоминание": {"required": [], "subject": "Напоминание",
                        "body": "Ждём отгрузку до {{срок}}."},
    })
    sent, drafted = outbox
    result = mail_policy.send_or_draft("s@example.ru", "", "", template="напоминание", values={})

    assert result["action"] == "drafted"
    assert sent == []


def test_reply_keeps_the_conversation_together(outbox, approved):
    """Ответ обязан уйти в ту же ветку, иначе переписка с фабрикой рассыпается."""
    sent, _ = outbox
    mail_policy.send_or_draft(
        "supplier@example.ru", "", "", template="первичный_запрос",
        values={"компания": "A", "категория": "B"},
        thread_id="thread-9", reply_to_message_id="msg-9")

    assert sent[0]["thread_id"] == "thread-9"
    assert sent[0]["reply_to_message_id"] == "msg-9"


def test_reason_is_always_explained(outbox, approved):
    """«Письмо не ушло» без причины выглядит как поломка системы."""
    sent, drafted = outbox
    for kwargs in ({"subject": "x", "body": "y"},
                   {"template": "нет_такого", "subject": "x", "body": "y"}):
        result = mail_policy.send_or_draft("s@example.ru", **kwargs)
        assert result["reason"], result
