"""Общая очередь отправки — разные каналы. Чужую строку воркер не трогает.

19.08.2026 телеграмный обход выгреб строки канала Авито и пометил их «бизнес-подключение
этого диалога больше не существует»: его проверка Telegram-соединения ничего не знает о
других источниках, и сообщения людям молча не ушли. Здесь это закрыто с обеих сторон.
"""
from __future__ import annotations

import pytest


def test_telegram_worker_returns_foreign_rows_to_the_queue(app_module, monkeypatch):
    import funnel_telegram_gateway as gateway

    released: list[dict] = []
    processed: list[int] = []

    class _Store:
        def claim_outbox(self, **kw):
            return [
                {"id": 1, "source_key": "telegram"},
                {"id": 2, "source_key": "avito"},
                {"id": 3, "source_key": "telegram_bot"},
            ]

        def finish_outbox(self, outbox_id, **kw):
            released.append({"id": outbox_id, **kw})

    monkeypatch.setattr(gateway, "_store", lambda: _Store())
    monkeypatch.setattr(gateway, "_process_outbox_item",
                        lambda row, **kw: processed.append(row["id"]))

    handled = gateway.process_outbox_once(worker_id="tg-1")

    assert processed == [1, 3], "телеграмный воркер обязан обработать только свои строки"
    assert handled == 2
    assert [item["id"] for item in released] == [2]
    # Возвращена в очередь, а не провалена: её ждёт воркер своего канала.
    assert released[0]["result"] == "pending"


def test_avito_worker_returns_foreign_rows_too(app_module, client, monkeypatch):
    import avito_channel

    monkeypatch.setenv("AVITO_WORKER_TOKEN", "т")
    released: list[tuple] = []
    monkeypatch.setattr(avito_channel.store, "claim_outbox", lambda **kw: [
        {"id": 9, "conversation_id": 1, "source_key": "telegram", "business_connection_id": "",
         "external_chat_id": "77", "text": "чужое", "author_type": "agent"},
    ])
    monkeypatch.setattr(avito_channel.store, "finish_outbox",
                        lambda outbox_id, **kw: released.append((outbox_id, kw.get("result"))))

    response = client.post("/api/avito-worker/outbox/claim",
                           json={"worker_id": "w1", "account": "main"},
                           headers={"X-Avito-Worker-Token": "т"})

    assert response.get_json()["items"] == []
    assert released == [(9, "pending")]
