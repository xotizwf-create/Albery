from __future__ import annotations

from datetime import datetime, timezone

import pytest

from scripts import import_legacy_telegram_history as importer


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)


def row(**overrides):
    base = {
        "id": 1,
        "created_at": NOW,
        "bot": "albery-ai-bot",
        "dialog_id": "212850563",
        "tg_user_id": 212850563,
        "username": "yulia1344",
        "display_name": "Юлия",
        "direction": "in",
        "kind": "lead_chat",
        "text": "Здравствуйте, интересуют условия",
        "tg_message_id": 501,
        "status": "ok",
    }
    base.update(overrides)
    return base


def test_import_never_sends_anything_to_clients(monkeypatch):
    """Перенос истории обязан писать только в журнал: очередь отправки не трогается,
    иначе клиенты получат сообщения из прошлого."""
    calls = []

    def fake_ingest(**kwargs):
        calls.append(kwargs)
        return {"conversation": {"id": 7}}

    monkeypatch.setattr(importer.store, "ingest_business_message", fake_ingest)
    monkeypatch.setattr(
        importer.store,
        "enqueue_outgoing_agent",
        lambda *args, **kwargs: pytest.fail("импорт не имеет права отправлять"),
    )
    monkeypatch.setattr(
        importer.store,
        "enqueue_outgoing_operator",
        lambda *args, **kwargs: pytest.fail("импорт не имеет права отправлять"),
    )

    result = importer.import_rows([row()], connection_id="bc-1", dry_run=False)

    assert result["imported"] == 1
    assert calls[0]["schedule_ai"] is False
    assert calls[0]["business_connection_id"] == "bc-1"


def test_incoming_is_client_and_outgoing_is_the_agent(monkeypatch):
    calls = []
    monkeypatch.setattr(
        importer.store,
        "ingest_business_message",
        lambda **kwargs: calls.append(kwargs) or {"conversation": {"id": 7}},
    )

    importer.import_rows(
        [row(direction="in"), row(id=2, direction="out", text="Высылаю условия")],
        connection_id="bc-1",
        dry_run=False,
    )

    assert calls[0]["author_type"] == "client"
    assert calls[1]["author_type"] == "agent"


def test_undelivered_answers_are_not_imported(monkeypatch):
    """Ответ со статусом ошибки клиент не получил — показывать его доставленным нельзя."""
    calls = []
    monkeypatch.setattr(
        importer.store,
        "ingest_business_message",
        lambda **kwargs: calls.append(kwargs) or {"conversation": {"id": 7}},
    )

    result = importer.import_rows(
        [row(direction="out", status="error"), row(id=2, direction="in")],
        connection_id="bc-1",
        dry_run=False,
    )

    assert result["skipped_failed"] == 1
    assert len(calls) == 1
    assert calls[0]["author_type"] == "client"


def test_a_failed_incoming_message_is_still_imported(monkeypatch):
    """Статус ошибки на входящем относится к нашей обработке, а не к тому, что клиент
    написал: его сообщение существует и должно быть в переписке."""
    calls = []
    monkeypatch.setattr(
        importer.store,
        "ingest_business_message",
        lambda **kwargs: calls.append(kwargs) or {"conversation": {"id": 7}},
    )

    result = importer.import_rows([row(direction="in", status="error")], connection_id="bc-1", dry_run=False)

    assert result["imported"] == 1
    assert result["skipped_failed"] == 0


def test_repeated_import_reuses_the_same_message_identity(monkeypatch):
    """Повторный запуск не должен раздваивать переписку: идентификатор сообщения
    берётся из Telegram, а при его отсутствии — из строки старого журнала."""
    calls = []
    monkeypatch.setattr(
        importer.store,
        "ingest_business_message",
        lambda **kwargs: calls.append(kwargs) or {"conversation": {"id": 7}},
    )

    importer.import_rows(
        [row(tg_message_id=501), row(id=9, tg_message_id=None)],
        connection_id="bc-1",
        dry_run=False,
    )

    assert calls[0]["external_message_id"] == "501"
    assert calls[1]["external_message_id"] == "legacy-9"


def test_dry_run_writes_nothing(monkeypatch):
    monkeypatch.setattr(
        importer.store,
        "ingest_business_message",
        lambda **kwargs: pytest.fail("холостой прогон не пишет в базу"),
    )

    result = importer.import_rows([row(), row(id=2)], connection_id="bc-1", dry_run=True)

    assert result["imported"] == 2
    assert result["conversations"] == {}
