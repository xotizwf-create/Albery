"""Очередь отправки одна на все каналы — каждый воркер забирает ТОЛЬКО свои строки.

19.08.2026 телеграмный обход забрал в аренду пять строк канала Авито и завалил их своей
проверкой Telegram-соединения: сообщения людям молча не ушли. Первая попытка починки —
«отпустить чужую строку обратно» — оказалась негодной: finish_outbox принимает только
окончательные исходы, поэтому строки просто зависли под чужой арендой. Правильная граница
стоит в САМОМ запросе: чужое не попадает в аренду вообще.
"""
from __future__ import annotations

import pytest


def test_telegram_worker_claims_only_telegram_sources(app_module, monkeypatch):
    import funnel_telegram_gateway as gateway

    seen: dict = {}

    class _Store:
        def claim_outbox(self, **kw):
            seen.update(kw)
            return []

    monkeypatch.setattr(gateway, "_store", lambda: _Store())

    gateway.process_outbox_once(worker_id="tg-1")

    assert set(seen["source_keys"]) == {"telegram", "telegram_bot"}
    assert "avito" not in set(seen["source_keys"])


def test_avito_door_claims_only_avito(app_module, client, monkeypatch):
    import avito_channel

    monkeypatch.setenv("AVITO_WORKER_TOKEN", "т")
    seen: dict = {}
    monkeypatch.setattr(avito_channel.store, "claim_outbox",
                        lambda **kw: seen.update(kw) or [])

    client.post("/api/avito-worker/outbox/claim", json={"worker_id": "w1", "account": "main"},
                headers={"X-Avito-Worker-Token": "т"})

    assert list(seen["source_keys"]) == ["avito"]


def test_claim_query_filters_by_source_in_sql(app_module, monkeypatch):
    """Фильтр обязан стоять в запросе: разбор после выдачи оставляет чужую строку в аренде."""
    import funnel_workspace_store as store

    statements: list[tuple[str, tuple]] = []

    class _Cur:
        rowcount = 0

        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def execute(self, sql, params=()):
            statements.append((" ".join(str(sql).split()), params))
        def fetchall(self): return []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def transaction(self): return self
        def cursor(self): return _Cur()

    monkeypatch.setattr(store, "pg_connect", lambda: _Conn())
    store.claim_outbox(worker_id="w1", source_keys=["avito"])

    claim = next((sql, params) for sql, params in statements if "WITH candidates" in sql)
    assert "o.source_key = ANY(%s::text[])" in claim[0]
    assert ["avito"] in claim[1]


def test_no_filter_means_every_channel(app_module, monkeypatch):
    """Без списка источников поведение прежнее — иначе тихо сломались бы старые вызовы."""
    import funnel_workspace_store as store

    statements: list[tuple[str, tuple]] = []

    class _Cur:
        rowcount = 0

        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def execute(self, sql, params=()):
            statements.append((" ".join(str(sql).split()), params))
        def fetchall(self): return []

    class _Conn:
        def __enter__(self): return self
        def __exit__(self, *_e): return False
        def transaction(self): return self
        def cursor(self): return _Cur()

    monkeypatch.setattr(store, "pg_connect", lambda: _Conn())
    store.claim_outbox(worker_id="w1")

    claim = next((sql, params) for sql, params in statements if "WITH candidates" in sql)
    assert None in claim[1]


def test_pending_is_not_a_valid_delivery_result():
    """Тот самый капкан: «вернуть в очередь» через finish_outbox невозможно."""
    from funnel_workspace_store import VALID_DELIVERY_RESULTS

    assert "pending" not in VALID_DELIVERY_RESULTS
    assert {"sent", "failed", "unknown", "cancelled"} <= set(VALID_DELIVERY_RESULTS)
