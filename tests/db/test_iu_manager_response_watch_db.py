from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

import pytest

from config import MSK_TZ
import funnel_workspace_store as store
import iu_manager_response_watch as watch
from shared.db import connect


pytestmark = pytest.mark.db


def _cleanup(source_key: str) -> None:
    with connect() as connection:
        with connection.cursor() as cur:
            cur.execute(
                "DELETE FROM funnel_workspace_conversations WHERE source_key = %s",
                (source_key,),
            )
            cur.execute(
                "DELETE FROM funnel_workspace_sources WHERE source_key = %s",
                (source_key,),
            )


def _client_waiting(
    source_key: str,
    *,
    occurred_at: datetime,
    name: str = "Александр",
) -> tuple[int, int]:
    chat_id = str(810_000_000 + int(uuid4().hex[:5], 16))
    first = store.ingest_business_message(
        source_key=source_key,
        external_chat_id=chat_id,
        external_message_id=f"client-{uuid4().hex}",
        text="Мне нужен менеджер",
        author_type="client",
        business_connection_id="",
        external_user_id=int(chat_id),
        display_name=name,
        occurred_at=occurred_at,
    )
    conversation_id = int(first["conversation"]["id"])
    store.mark_waiting_human(
        conversation_id,
        expected_version=int(first["conversation"]["state_version"]),
        reason="Клиент ждёт менеджера.",
        manager_requested=True,
        permanent_human=True,
    )
    return conversation_id, int(first["message"]["id"])


def _alerts(conversation_id: int) -> list[dict]:
    with connect() as connection:
        with connection.cursor() as cur:
            cur.execute(
                """
                SELECT kind, status
                  FROM iu_manager_wait_alerts
                 WHERE conversation_id = %s
                 ORDER BY id
                """,
                (conversation_id,),
            )
            return [dict(row) for row in cur.fetchall()]


def test_alerts_fire_once_at_10_30_60_and_ignore_bot_acknowledgements():
    suffix = uuid4().hex[:10]
    source_key = f"test-iu-manager-watch-{suffix}"
    start = datetime(2026, 7, 30, 10, 0, tzinfo=MSK_TZ)
    sent: list[str] = []
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="IU manager watch",
        )
        conversation_id, _ = _client_waiting(
            source_key,
            occurred_at=start,
        )

        # A service acknowledgement from the bot is not a manager answer.
        current = store.get_conversation(conversation_id)
        store.enqueue_outgoing_agent(
            conversation_id,
            text=watch.AFTER_HOURS_CLIENT_REPLY,
            expected_version=int(current["state_version"]),
            idempotency_key=f"test-ack-{suffix}",
            metadata={"service_reply": True},
        )

        for minute, expected in ((10, "10 минут"), (30, "30 минут"), (60, "60 минут")):
            watch.process_once(
                worker_id=f"worker-{minute}",
                notify=lambda text: sent.append(text),
                now=start + timedelta(minutes=minute),
                source_key=source_key,
                connect_factory=connect,
            )
            assert expected in sent[-1]

        assert len(sent) == 3
        assert [row["status"] for row in _alerts(conversation_id)] == [
            "sent",
            "sent",
            "sent",
        ]

        # Replaying the worker cannot duplicate already delivered alerts.
        assert (
            watch.process_once(
                worker_id="worker-repeat",
                notify=lambda text: sent.append(text),
                now=start + timedelta(minutes=61),
                source_key=source_key,
                connect_factory=connect,
            )
            == 0
        )
        assert len(sent) == 3
    finally:
        _cleanup(source_key)


def test_real_operator_reply_cancels_a_pending_alert():
    suffix = uuid4().hex[:10]
    source_key = f"test-iu-manager-answer-{suffix}"
    start = datetime(2026, 7, 30, 10, 0, tzinfo=MSK_TZ)
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="IU manager answer",
        )
        conversation_id, _ = _client_waiting(
            source_key,
            occurred_at=start,
        )
        assert (
            watch.sync_due_alerts(
                now=start + timedelta(minutes=10),
                source_key=source_key,
                connect_factory=connect,
            )
            == 1
        )

        current = store.get_conversation(conversation_id)
        store.enqueue_outgoing_operator(
            conversation_id,
            text="Добрый день! Отвечаю на ваш вопрос.",
            expected_version=int(current["state_version"]),
            idempotency_key=f"test-operator-{suffix}",
            operator_name="Менеджер",
        )
        with connect() as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE funnel_workspace_messages
                       SET delivery_status = 'sent'
                     WHERE conversation_id = %s
                       AND author_type = 'operator'
                    """,
                    (conversation_id,),
                )

        watch.sync_due_alerts(
            now=start + timedelta(minutes=30),
            source_key=source_key,
            connect_factory=connect,
        )

        assert _alerts(conversation_id) == [
            {"kind": "10m", "status": "cancelled"}
        ]
    finally:
        _cleanup(source_key)


def test_silent_ai_lead_is_escalated_but_a_delivered_ai_answer_closes_wait():
    suffix = uuid4().hex[:10]
    source_key = f"test-iu-ai-wait-{suffix}"
    start = datetime(2026, 7, 30, 10, 0, tzinfo=MSK_TZ)
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="IU silent AI watch",
        )
        chat_id = str(820_000_000 + int(uuid4().hex[:5], 16))
        first = store.ingest_business_message(
            source_key=source_key,
            external_chat_id=chat_id,
            external_message_id=f"client-{suffix}",
            text="ИИ пока не ответил",
            author_type="client",
            business_connection_id="",
            external_user_id=int(chat_id),
            display_name="Клиент ИИ",
            occurred_at=start,
        )
        conversation_id = int(first["conversation"]["id"])

        assert (
            watch.sync_due_alerts(
                now=start + timedelta(minutes=10),
                source_key=source_key,
                connect_factory=connect,
            )
            == 1
        )

        current = store.get_conversation(conversation_id)
        answer = store.enqueue_outgoing_agent(
            conversation_id,
            text="Теперь ответ доставлен.",
            expected_version=int(current["state_version"]),
            idempotency_key=f"test-ai-answer-{suffix}",
        )
        with connect() as connection:
            with connection.cursor() as cur:
                cur.execute(
                    """
                    UPDATE funnel_workspace_messages
                       SET delivery_status = 'sent'
                     WHERE id = %s
                    """,
                    (int(answer["message"]["id"]),),
                )

        watch.sync_due_alerts(
            now=start + timedelta(minutes=30),
            source_key=source_key,
            connect_factory=connect,
        )

        assert _alerts(conversation_id) == [
            {"kind": "10m", "status": "cancelled"}
        ]
    finally:
        _cleanup(source_key)


def test_stop_command_suppresses_manager_wait_notifications():
    suffix = uuid4().hex[:10]
    source_key = f"test-iu-manager-stop-{suffix}"
    start = datetime(2026, 7, 30, 10, 0, tzinfo=MSK_TZ)
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="IU manager stop",
        )
        conversation_id, _ = _client_waiting(
            source_key,
            occurred_at=start,
        )
        current = store.get_conversation(conversation_id)
        store.enqueue_outgoing_agent(
            conversation_id,
            text="Поддержка и напоминания остановлены.",
            expected_version=int(current["state_version"]),
            idempotency_key=f"test-stop-{suffix}",
            metadata={"iu_event": "stop"},
            service=True,
        )

        assert (
            watch.sync_due_alerts(
                now=start + timedelta(minutes=30),
                source_key=source_key,
                connect_factory=connect,
            )
            == 0
        )
        assert _alerts(conversation_id) == []
    finally:
        _cleanup(source_key)


def test_night_is_silent_and_next_morning_is_one_prioritized_summary():
    suffix = uuid4().hex[:10]
    source_key = f"test-iu-manager-morning-{suffix}"
    evening = datetime(2026, 7, 29, 20, 0, tzinfo=MSK_TZ)
    morning = datetime(2026, 7, 30, 9, 0, tzinfo=MSK_TZ)
    sent: list[str] = []
    try:
        store.ensure_source(
            source_key,
            source_type="test",
            display_name="IU manager morning",
        )
        first_id, _ = _client_waiting(
            source_key,
            occurred_at=evening,
            name="Первый",
        )
        second_id, _ = _client_waiting(
            source_key,
            occurred_at=evening + timedelta(minutes=30),
            name="Второй",
        )

        assert (
            watch.process_once(
                worker_id="worker-night",
                notify=lambda text: sent.append(text),
                now=evening + timedelta(minutes=60),
                source_key=source_key,
                connect_factory=connect,
            )
            == 0
        )
        assert sent == []
        assert _alerts(first_id) == []
        assert _alerts(second_id) == []

        assert (
            watch.process_once(
                worker_id="worker-morning",
                notify=lambda text: sent.append(text),
                now=morning,
                source_key=source_key,
                connect_factory=connect,
            )
            == 2
        )
        assert len(sent) == 1
        assert sent[0].index("Клиент Первый") < sent[0].index("Клиент Второй")
        assert _alerts(first_id) == [{"kind": "morning", "status": "sent"}]
        assert _alerts(second_id) == [{"kind": "morning", "status": "sent"}]
    finally:
        _cleanup(source_key)
