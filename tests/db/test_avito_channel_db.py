"""Канал Авито на настоящем PostgreSQL: у продавца должен быть ОДИН разговор.

Здесь проверяется то, чего поддельный курсор не проверит: ключ уникальности, переезд
сообщений и итоговая картина в базе после зеркалирования. Живой случай 19.08.2026 — агент
написал первым автору объявления 4297041572, сообщение ушло, а разговор остался с временным
ключом `item:4297041572`, потому что со страницы объявления Авито в чат не переходит.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

import funnel_workspace_store as store
from shared.db import connect

pytestmark = pytest.mark.db


@pytest.fixture()
def avito(app_module):
    import avito_channel

    return avito_channel


@pytest.fixture()
def account() -> str:
    """Свой аккаунт на каждый тест: разговоры разных тестов не должны видеть друг друга."""
    return f"t{uuid4().hex[:10]}"


def _outreach(account: str, listing_id: str, *, chat_id: str | None = None) -> dict:
    store.ensure_source("avito", source_type="avito_web", display_name="Авито")
    return store.ensure_conversation(
        external_chat_id=chat_id or f"item:{listing_id}",
        source_key="avito",
        business_connection_id=account,
        display_name=f"Объявление {listing_id}",
        metadata={"listing": {"id": listing_id}, "outreach": True},
    )


def _conversations(account: str) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, external_chat_id, status, metadata FROM funnel_workspace_conversations "
                "WHERE source_key = 'avito' AND business_connection_id = %s ORDER BY id",
                (account,),
            )
            return [dict(row) for row in cur.fetchall()]


def _messages(conversation_id: int) -> list[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, text, author_type FROM funnel_workspace_messages "
                "WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def _outbox(conversation_id: int) -> list[tuple[str, str]]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT external_chat_id, delivery_status FROM funnel_workspace_outbox "
                "WHERE conversation_id = %s ORDER BY id",
                (conversation_id,),
            )
            return [(row["external_chat_id"], row["delivery_status"]) for row in cur.fetchall()]


def test_the_temporary_conversation_becomes_the_real_chat(avito, account):
    temporary = _outreach(account, "4297041572")

    answer = avito.ingest_inbound({
        "account": account,
        "external_chat_id": "u2i-nastoyaschiy-1",
        "display_name": "Продавец",
        "listing": {"id": "4297041572", "title": "Генератор бензиновый 3,3 кВт"},
        "messages": [{"external_message_id": "m-1", "text": "Да, продаю",
                      "author_type": "client"}],
    })

    assert answer["conversation_id"] == int(temporary["id"])
    assert answer["stitched"]["action"] == "adopted"
    rows = _conversations(account)
    assert [row["external_chat_id"] for row in rows] == ["u2i-nastoyaschiy-1"]
    assert [m["text"] for m in _messages(int(temporary["id"]))] == ["Да, продаю"]


def test_a_chat_about_another_listing_stays_a_separate_conversation(avito, account):
    temporary = _outreach(account, "4297041572")

    avito.ingest_inbound({
        "account": account,
        "external_chat_id": "u2i-drugoy",
        "listing": {"id": "8288883000", "title": "Другое объявление"},
        "messages": [{"external_message_id": "m-2", "text": "Здравствуйте",
                      "author_type": "client"}],
    })

    keys = sorted(row["external_chat_id"] for row in _conversations(account))
    assert keys == ["item:4297041572", "u2i-drugoy"]
    assert _messages(int(temporary["id"])) == []


def test_a_duplicate_conversation_is_folded_into_the_real_one(avito, account):
    """Дубль, заведённый до починки, лечится на первом же обходе."""
    temporary = _outreach(account, "4297041572")
    real = _outreach(account, "4297041572", chat_id="u2i-nastoyaschiy-2")
    store.enqueue_outgoing_operator(
        int(temporary["id"]), text="Здравствуйте! Ещё продаёте?",
        expected_version=int(temporary["state_version"]), operator_name="Оператор",
        idempotency_key=f"test-{uuid4()}",
    )

    avito.ingest_inbound({
        "account": account,
        "external_chat_id": "u2i-nastoyaschiy-2",
        "listing": {"id": "4297041572"},
        "messages": [{"external_message_id": "m-3", "text": "Продаю", "author_type": "client"}],
    })

    rows = {row["id"]: row for row in _conversations(account)}
    assert rows[int(temporary["id"])]["status"] == "closed"
    assert rows[int(temporary["id"])]["metadata"]["merged_into"] == int(real["id"])
    # Переписка не рвётся пополам: оба сообщения лежат в настоящем разговоре.
    assert [m["text"] for m in _messages(int(real["id"]))] == ["Здравствуйте! Ещё продаёте?",
                                                              "Продаю"]
    assert _messages(int(temporary["id"])) == []
    # Неотправленное письмо едет вместе с перепиской — и уже в настоящий чат.
    assert _outbox(int(real["id"])) == [("u2i-nastoyaschiy-2", "pending")]


def test_the_existing_chat_about_a_listing_is_found_for_a_new_outreach(avito, account):
    real = _outreach(account, "4297041572", chat_id="u2i-nastoyaschiy-3")

    found = avito.find_conversation_by_listing(account=account, listing_id="4297041572")

    assert found is not None and int(found["id"]) == int(real["id"])


def test_an_ambiguous_listing_is_not_guessed(avito, account):
    """У ПРОДАВЦА об одном объявлении пишут разные покупатели — угадывать нельзя."""
    _outreach(account, "4297041572", chat_id="u2i-pervyy")
    _outreach(account, "4297041572", chat_id="u2i-vtoroy")

    assert avito.find_conversation_by_listing(account=account, listing_id="4297041572") is None
