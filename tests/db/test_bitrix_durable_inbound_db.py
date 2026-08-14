from __future__ import annotations

import uuid

import pytest


pytestmark = pytest.mark.db


def test_capture_dedup_batch_and_ambiguous_delivery_boundary():
    import bitrix_inbound as queue

    suffix = uuid.uuid4().hex
    scope = f"chat:test:{suffix}"
    event_one = f"chat:test:{suffix}:1"
    event_two = f"chat:test:{suffix}:2"
    ids: list[str] = []
    try:
        first = queue.enqueue(
            event_key=event_one,
            event_kind="chat_message",
            scope_key=scope,
            payload={
                "message_id": 8_800_000_000_000_000 + int(suffix[:8], 16),
                "bot_id": 24,
                "dialog_id": "999999",
                "from_user_id": 999999,
                "message_text": "one",
                "auth[access_token]": "must-not-persist",
            },
        )
        duplicate = queue.enqueue(
            event_key=event_one,
            event_kind="chat_message",
            scope_key=scope,
            payload={"message_id": 1, "message_text": "changed"},
        )
        second = queue.enqueue(
            event_key=event_two,
            event_kind="chat_message",
            scope_key=scope,
            payload={
                "message_id": 8_900_000_000_000_000 + int(suffix[:8], 16),
                "bot_id": 24,
                "dialog_id": "999999",
                "from_user_id": 999999,
                "message_text": "two",
            },
        )
        ids.extend((str(first["id"]), str(second["id"])))
        assert first["inserted"] is True
        assert duplicate["inserted"] is False
        assert str(duplicate["id"]) == str(first["id"])

        owner = queue.worker_id(99)
        batch = queue.claim_next(owner)
        assert batch is not None
        assert batch["status"] == "queued"
        assert len(batch["rows"]) == 2
        assert "auth[access_token]" not in batch["rows"][0]["payload"]

        prepared = {"action": "chat_reply", "dialog_id": "999999", "bot_id": 24}
        queue.mark_brain_running(batch["batch_id"], owner, prepared)
        queue.store_answer(batch["batch_id"], owner, "stored answer", prepared=prepared)

        # The worker which produced the answer retains the lease and may deliver immediately.
        # A second worker must not be able to claim the same stored answer concurrently.
        assert queue.claim_next(queue.worker_id(100)) is None
        queue.mark_sending(batch["batch_id"], owner)
        assert queue.mark_delivery_failure(
            batch["batch_id"], owner, "known rejection", ambiguous=False,
        ) == "delivery_retry"

        assert queue.claim_next(owner) is None
        with queue._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE bitrix_inbound_jobs SET available_at=now() WHERE batch_id=%s",
                    (batch["batch_id"],),
                )
        delivery = queue.claim_next(owner)
        assert delivery is not None
        assert delivery["status"] == "delivery_retry"
        queue.mark_sending(delivery["batch_id"], owner)
        assert queue.mark_delivery_failure(
            delivery["batch_id"], owner, "timeout after send", ambiguous=True,
        ) == "review"

        with queue._db() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT status, count(*) AS n FROM bitrix_inbound_jobs "
                    "WHERE id::text = ANY(%s) GROUP BY status",
                    (ids,),
                )
                rows = cur.fetchall()
        assert [(row["status"], int(row["n"])) for row in rows] == [("review", 2)]
    finally:
        if ids:
            with queue._db() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM bitrix_inbound_jobs WHERE id::text = ANY(%s)", (ids,))
