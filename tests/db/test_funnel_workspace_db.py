"""Real PostgreSQL checks for the durable funnel workspace schema."""

from __future__ import annotations

from uuid import uuid4

import pytest

import funnel_workspace_store as store
from shared.db import connect


pytestmark = pytest.mark.db


def test_workspace_tables_and_fk_indexes_exist():
    expected_tables = set(store.SCHEMA_TABLES)
    expected_indexes = {
        "idx_fwc_last_message",
        "idx_fwu_conversation",
        "idx_fwu_message",
        "idx_fwu_business_head",
        "idx_fwu_bot_head",
        "idx_fwaj_conversation",
        "idx_fwaj_trigger_message",
        "idx_fwaj_outbox",
        "idx_fwca_claim",
        "idx_fwca_expired_lease",
        "idx_fwca_conversation",
        "idx_fwca_message",
        "uq_fwca_ensure_conversation",
    }
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT table_name
                  FROM information_schema.tables
                 WHERE table_schema = 'public'
                   AND table_name = ANY(%s)
                """,
                (list(expected_tables),),
            )
            tables = {row["table_name"] for row in cur.fetchall()}
            cur.execute(
                """
                SELECT indexname
                  FROM pg_indexes
                 WHERE schemaname = 'public'
                   AND indexname = ANY(%s)
                """,
                (list(expected_indexes),),
            )
            indexes = {row["indexname"] for row in cur.fetchall()}

    assert tables == expected_tables
    assert indexes == expected_indexes


def test_update_lanes_keep_business_head_of_line_without_bot_blocking():
    suffix = uuid4().hex
    source_key = f"test-update-lanes-{suffix}"
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="Update lane DB test",
        )
        first = store.capture_update(
            source_key=source_key,
            external_update_id=f"{suffix}-1",
            payload={"business_message": {"message_id": 1}},
        )
        second = store.capture_update(
            source_key=source_key,
            external_update_id=f"{suffix}-2",
            payload={"edited_business_message": {"message_id": 1}},
        )
        bot = store.capture_update(
            source_key=source_key,
            external_update_id=f"{suffix}-3",
            payload={"message": {"message_id": 3}},
        )
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE funnel_workspace_updates
                       SET processing_status = 'retry',
                           available_at = now() + interval '1 hour'
                     WHERE id = %s
                    """,
                    (first["id"],),
                )

        assert store.claim_updates(
            worker_id=f"business-{suffix}",
            lane="business",
            source_key=source_key,
        ) == []

        bot_claim = store.claim_updates(
            worker_id=f"bot-{suffix}",
            lane="bot",
            source_key=source_key,
        )
        assert [int(row["id"]) for row in bot_claim] == [int(bot["id"])]
        store.complete_update(bot["id"], worker_id=f"bot-{suffix}")

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE funnel_workspace_updates
                       SET available_at = now()
                     WHERE id = %s
                    """,
                    (first["id"],),
                )
        first_claim = store.claim_updates(
            worker_id=f"business-{suffix}",
            lane="business",
            source_key=source_key,
        )
        assert [int(row["id"]) for row in first_claim] == [int(first["id"])]
        store.complete_update(first["id"], worker_id=f"business-{suffix}")
        second_claim = store.claim_updates(
            worker_id=f"business-{suffix}",
            lane="business",
            source_key=source_key,
        )
        assert [int(row["id"]) for row in second_claim] == [int(second["id"])]
    finally:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM funnel_workspace_updates WHERE source_key = %s",
                    (source_key,),
                )
                cur.execute(
                    "DELETE FROM funnel_workspace_sources WHERE source_key = %s",
                    (source_key,),
                )


def test_latest_history_and_read_watermark_keep_newer_messages_unread():
    suffix = uuid4().hex
    source_key = f"test-workspace-{suffix}"
    conversation_id: int | None = None
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="Workspace DB test",
        )
        conversation = store.ensure_conversation(
            source_key=source_key,
            external_chat_id=f"chat-{suffix}",
            business_connection_id=f"connection-{suffix}",
            external_user_id=9_000_000_001,
            display_name="DB test",
        )
        conversation_id = int(conversation["id"])

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO funnel_workspace_messages (
                        conversation_id, external_message_id, author_type,
                        direction, text, delivery_status, occurred_at
                    )
                    SELECT %s, 'db-' || item::text, 'client', 'inbound',
                           'message ' || item::text, 'sent',
                           now() + item * interval '1 millisecond'
                      FROM generate_series(1, 205) AS item
                 RETURNING id
                    """,
                    (conversation_id,),
                )
                message_ids = [int(row["id"]) for row in cur.fetchall()]
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET unread_count = 205,
                           last_message_id = %s,
                           last_message_at = now(),
                           last_message_text = 'message 205',
                           last_author_type = 'client'
                     WHERE id = %s
                    """,
                    (message_ids[-1], conversation_id),
                )

        latest = store.list_messages(conversation_id, limit=200)
        assert [int(row["id"]) for row in latest] == message_ids[-200:]

        marked = store.mark_read(
            conversation_id,
            through_message_id=message_ids[-2],
        )
        assert int(marked["unread_count"]) == 1

        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO funnel_workspace_messages (
                        conversation_id, external_message_id, author_type,
                        direction, text, delivery_status
                    )
                    VALUES (%s, %s, 'client', 'inbound', 'new after fetch', 'sent')
                 RETURNING id
                    """,
                    (conversation_id, f"db-new-{suffix}"),
                )
                newer_id = int(cur.fetchone()["id"])
                cur.execute(
                    """
                    UPDATE funnel_workspace_conversations
                       SET unread_count = unread_count + 1,
                           last_message_id = %s,
                           last_message_at = now(),
                           last_message_text = 'new after fetch',
                           last_author_type = 'client'
                     WHERE id = %s
                    """,
                    (newer_id, conversation_id),
                )

        marked_again = store.mark_read(
            conversation_id,
            through_message_id=message_ids[-2],
        )
        assert int(marked_again["unread_count"]) == 2
        assert int(marked_again["last_read_message_id"]) == message_ids[-2]
    finally:
        with connect() as conn:
            with conn.cursor() as cur:
                if conversation_id is not None:
                    cur.execute(
                        "DELETE FROM funnel_workspace_conversations WHERE id = %s",
                        (conversation_id,),
                    )
                cur.execute(
                    "DELETE FROM funnel_workspace_sources WHERE source_key = %s",
                    (source_key,),
                )


def test_confirmed_outbox_delivery_drives_durable_crm_action_lifecycle():
    suffix = uuid4().hex
    source_key = f"test-crm-action-{suffix}"
    conversation_id: int | None = None
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="CRM action DB test",
        )
        conversation = store.ensure_conversation(
            source_key=source_key,
            external_chat_id=f"chat-{suffix}",
            business_connection_id=f"connection-{suffix}",
            external_user_id=9_000_000_002,
            display_name="CRM action DB test",
        )
        conversation_id = int(conversation["id"])
        queued = store.enqueue_outgoing_agent(
            conversation_id,
            text="Условия отправлены",
            expected_version=conversation["state_version"],
            idempotency_key=f"db-crm-action-{suffix}",
            metadata={"stage_move": "C16:TERMS"},
        )
        outbox_id = int(queued["outbox"]["id"])

        claimed_outbox = store.claim_outbox(
            worker_id=f"db-outbox-{suffix}",
            limit=100,
        )
        assert outbox_id in {int(row["id"]) for row in claimed_outbox}
        boundary = store.begin_outbox_send(
            outbox_id,
            worker_id=f"db-outbox-{suffix}",
        )
        assert boundary["allowed"] is True
        finished = store.finish_outbox(
            outbox_id,
            worker_id=f"db-outbox-{suffix}",
            result="sent",
            provider_message_id=f"provider-{suffix}",
        )
        action = finished["crm_action"]
        assert action["processing_status"] == "pending"
        assert action["target_stage"] == "C16:TERMS"
        assert int(action["outbox_id"]) == outbox_id
        assert int(action["message_id"]) == int(queued["message"]["id"])

        claimed_actions = store.claim_crm_actions(
            worker_id=f"db-crm-{suffix}",
            limit=100,
        )
        action_id = int(action["id"])
        assert action_id in {int(row["id"]) for row in claimed_actions}
        completed = store.complete_crm_action(
            action_id,
            worker_id=f"db-crm-{suffix}",
            result={
                "status": "applied",
                "deal_id": 123,
                "target_stage": "C16:TERMS",
            },
        )
        assert completed["processing_status"] == "done"
        assert store.get_conversation(conversation_id)["stage_id"] == "C16:TERMS"
        assert store.list_crm_actions(
            conversation_id=conversation_id,
            processing_status="done",
        )[0]["id"] == action_id
    finally:
        with connect() as conn:
            with conn.cursor() as cur:
                if conversation_id is not None:
                    cur.execute(
                        "DELETE FROM funnel_workspace_conversations WHERE id = %s",
                        (conversation_id,),
                    )
                cur.execute(
                    "DELETE FROM funnel_workspace_sources WHERE source_key = %s",
                    (source_key,),
                )
